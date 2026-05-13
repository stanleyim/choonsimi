"""
fetch_dart.py — v2.5.0
────────────────────────────────────────────────────────────
v2.4 대비 변경:
  ✔ history.csv 최신날짜 필터 + 코드 중복 제거 (핵심 수정)
  ✔ csv.DictReader → pandas (다중날짜 누적 CSV 대응)
  ✔ load_name_map() 동일 수정
  ✔ TIMEOUT 5 → 15 (DART API 응답 지연 대응)
  ✔ 스킵 로직: 분기 기준 → 오늘 날짜 기준
────────────────────────────────────────────────────────────
"""

import os, json, time, zipfile, io
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

DART_BASE   = "https://opendart.fss.or.kr/api"
OUTPUT_PATH = "fundamental.json"
CORP_CACHE  = "corp_map_cache.json"
INPUT_CSV   = "history.csv"
KST         = timezone(timedelta(hours=9))
TARGET_SIZE = 200
SLEEP_SEC   = 0.10
TIMEOUT     = 15     # ✅ 5 → 15 (DART 응답 지연 대응)
WORKERS     = 3

REPRT_MAP = {
    "1Q": "11013", "2Q": "11012",
    "3Q": "11014", "4Q": "11011",
}


def get_fallback_quarters() -> list:
    now  = datetime.now(KST)
    y, m = now.year, now.month
    if   m <= 3: base = [(y-1,"4Q"),(y-1,"3Q")]
    elif m <= 6: base = [(y,  "1Q"),(y-1,"4Q")]
    elif m <= 9: base = [(y,  "2Q"),(y,  "1Q")]
    else:        base = [(y,  "3Q"),(y,  "2Q")]
    return base


def to_int(v) -> int:
    try:    return int(str(v or "0").replace(",","").strip() or "0")
    except: return 0


# ✅ 최신날짜 필터 + 코드 중복 제거
def load_name_map() -> dict:
    try:
        df = pd.read_csv(INPUT_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["code"]  = df["code"].str.zfill(6)
        latest_date = df["date"].max()
        df = df[df["date"] == latest_date].drop_duplicates("code")
        return dict(zip(df["code"], df["name"]))
    except: return {}


def get_dart_key() -> str:
    key = os.environ.get("DART_API_KEY","")
    if not key: raise RuntimeError("DART_API_KEY 환경변수 필요")
    return key


def get_corp_codes(key: str) -> dict:
    if os.path.exists(CORP_CACHE):
        try:
            with open(CORP_CACHE,"r",encoding="utf-8-sig") as f:
                cached = json.load(f)
            if len(cached) > 100:
                print(f"📦 corp_code 캐시 사용: {len(cached)}종목")
                return cached
        except: pass

    try:
        res = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": key}, timeout=30,
        )
        res.raise_for_status()
        zf   = zipfile.ZipFile(io.BytesIO(res.content))
        root = ET.fromstring(zf.read("CORPCODE.xml"))
        corp_map = {}
        for item in root.findall("list"):
            sc = item.findtext("stock_code","").strip()
            cc = item.findtext("corp_code", "").strip()
            if sc and len(sc) == 6:
                corp_map[sc] = cc
        with open(CORP_CACHE,"w",encoding="utf-8-sig") as f:
            json.dump(corp_map, f, ensure_ascii=False)
        print(f"📦 corp_code 매핑 갱신: {len(corp_map)}종목")
        return corp_map
    except Exception as e:
        print(f"⚠️ corp_code 다운로드 실패: {e}")
        return {}


def fetch_financial_one(key, corp_code, stock_code, year, reprt_code) -> dict:
    result = {"code": stock_code}
    try:
        data = None
        for fs_div in ["CFS","OFS"]:
            res = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key":  key,
                    "corp_code":  corp_code,
                    "bsns_year":  str(year),
                    "reprt_code": reprt_code,
                    "fs_div":     fs_div,
                },
                timeout=TIMEOUT,
            )
            try:    d = res.json()
            except: continue
            if d.get("status") == "000" and d.get("list"):
                data = d; break

        if not data: return {}

        items = data.get("list",[])
        found = {
            "equity":None,"total_debt":None,"net_income":None,
            "op_profit":None,"op_profit_prev":None
        }

        for item in items:
            acct = item.get("account_nm","").strip()
            cur  = to_int(item.get("thstrm_amount"))
            prev = to_int(item.get("frmtrm_amount"))
            if found["equity"]     is None and acct in ["자본총계","자본 합계"]:
                found["equity"]     = cur
            if found["total_debt"] is None and acct in ["부채총계","부채 합계"]:
                found["total_debt"] = cur
            if found["net_income"] is None and acct in [
                "당기순이익(손실)","당기순이익","분기순이익(손실)","분기순이익"]:
                found["net_income"] = cur
            if found["op_profit"]  is None and acct in ["영업이익(손실)","영업이익"]:
                found["op_profit"] = cur; found["op_profit_prev"] = prev

        eq, ni, td = found["equity"], found["net_income"], found["total_debt"]
        op, opp    = found["op_profit"], found["op_profit_prev"]

        if eq is not None: result["equity"]     = eq
        if td is not None: result["total_debt"] = td
        if ni is not None: result["net_income"] = ni

        result["op_growth"] = round((op-opp)/abs(opp)*100,2) \
            if op and opp and abs(opp) > 0 else 0

        if eq and abs(eq) > 0 and ni is not None:
            result["roe"]        = round(ni/eq*100, 2)
        if eq and abs(eq) > 0 and td is not None:
            result["debt_ratio"] = round(td/eq*100, 2)

        return result if len(result) > 1 else {}
    except: return {}


def fetch_financial_with_fallback(key, corp_code, stock_code, name_map) -> dict:
    quarters = get_fallback_quarters()
    for year, quarter in quarters:
        reprt_code = REPRT_MAP[quarter]
        data = fetch_financial_one(key, corp_code, stock_code, year, reprt_code)
        if data:
            data["year"]       = year
            data["quarter"]    = quarter
            data["reprt_code"] = reprt_code
            data["name"]       = name_map.get(stock_code, "")
            return data
        time.sleep(SLEEP_SEC)
    return {}


def run():
    print("[DART START]")

    try:    key = get_dart_key()
    except Exception as e:
        print(f"⛔ {e} → 스킵"); return

    today    = datetime.now(KST).strftime("%Y-%m-%d")
    quarters = get_fallback_quarters()
    year, quarter = quarters[0]

    # ✅ 스킵 로직: 오늘 날짜 기준 (분기 기준 제거)
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH,"r",encoding="utf-8-sig") as f:
                ex = json.load(f)
            if ex.get("date") == today and ex.get("count",0) >= 10:
                print(f"ℹ️ 오늘({today}) 이미 수집됨 ({ex['count']}종목) → 스킵")
                return
        except: pass

    # ✅ 최신날짜 필터 + 코드 중복 제거
    try:
        df = pd.read_csv(INPUT_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["code"]  = df["code"].str.zfill(6)
        latest_date = df["date"].max()
        df_latest   = df[df["date"] == latest_date].drop_duplicates("code")
        target      = df_latest["code"].tolist()[:TARGET_SIZE]
        print(f"[DATA] 기준일={latest_date} | 대상={len(target)}종목")
    except Exception as e:
        print(f"⚠️ {INPUT_CSV} 로드 실패: {e}"); return

    if not target:
        print("⚠️ 대상 종목 없음 → 스킵"); return

    corp_map = get_corp_codes(key)
    if not corp_map:
        print("⚠️ corp_map 없음 → 스킵"); return

    name_map = load_name_map()

    valid = [(c, corp_map[c]) for c in target if c in corp_map]
    print(f"🎯 대상: {len(valid)}종목 | fallback 순서: {[f'{y}{q}' for y,q in quarters[:3]]}")

    results, error_cnt, done = [], 0, 0

    def _fetch(args):
        code, corp_code = args
        return fetch_financial_with_fallback(key, corp_code, code, name_map)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_fetch,(c,cc)): c for c,cc in valid}
        for future in as_completed(futures):
            done += 1
            data = future.result()
            if data: results.append(data)
            else:    error_cnt += 1
            if done % 20 == 0:
                print(f"⏳ {done}/{len(valid)} 처리중... 성공={len(results)} 실패={error_cnt}")

    output = {
        "date":    today,
        "year":    year,
        "quarter": quarter,
        "count":   len(results),
        "errors":  error_cnt,
        "stocks":  results,
    }

    with open(OUTPUT_PATH,"w",encoding="utf-8-sig") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DART DONE] 성공={len(results)} 실패={error_cnt}")


if __name__ == "__main__":
    run()
