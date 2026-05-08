"""
fetch_dart.py — v2.2 (Option A Patch)
─────────────────────────────────────
history.csv 200종목 → DART 재무데이터 → fundamental.json
변경사항:
  ✅ 인코딩 utf-8-sig 통일 (모바일 한글 깨짐 방지)
  ✅ DART API 타임아웃 30초 상향 (응답 지연/네트워크 불안정 대비)
  ✅ 오류 발생 종목 상세 로깅 추가 (디버깅 용이)
환경변수: DART_API_KEY
─────────────────────────────────────
"""

import os, json, time, csv, zipfile, io, requests, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

DART_BASE = "https://opendart.fss.or.kr/api"
OUTPUT_PATH = "fundamental.json"
CORP_CACHE = "corp_map_cache.json"
INPUT_CSV = "history.csv"
KST = timezone(timedelta(hours=9))
TARGET_SIZE = 200
SLEEP_SEC = 0.35

def to_int(v) -> int:
    try: return int(str(v or "0").replace(",", "").strip() or "0")
    except: return 0

def get_dart_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key: raise RuntimeError("DART_API_KEY 환경변수 필요")
    return key

def get_corp_codes(key: str) -> dict:
    if os.path.exists(CORP_CACHE):
        try:
            with open(CORP_CACHE, "r", encoding="utf-8-sig") as f: return json.load(f)
        except: pass
    try:
        res = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
        res.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        root = ET.fromstring(zf.read("CORPCODE.xml"))
        corp_map = {}
        for item in root.findall("list"):
            stock_code = item.findtext("stock_code", "").strip()
            corp_code = item.findtext("corp_code", "").strip()
            if stock_code and len(stock_code) == 6:
                corp_map[stock_code] = corp_code
        with open(CORP_CACHE, "w", encoding="utf-8-sig") as f:
            json.dump(corp_map, f, ensure_ascii=False)        print(f"📦 corp_code 매핑 캐시 생성: {len(corp_map)}종목")
        return corp_map
    except Exception as e:
        print(f"⚠️ corp_code 다운로드 실패: {e}")
        return {}

def get_latest_quarter() -> tuple:
    now = datetime.now(KST)
    y, m = now.year, now.month
    if m <= 3: return y-1, "4Q"
    if m <= 6: return y, "1Q"
    if m <= 9: return y, "2Q"
    return y, "3Q"

def fetch_financial(key, corp_code, stock_code, year, reprt_code):
    result = {"code": stock_code}
    try:
        data = None
        for fs_div in ["CFS", "OFS"]:
            res = requests.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params={
                "crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year),
                "reprt_code": reprt_code, "fs_div": fs_div
            }, timeout=30)  # ✅ 15초 → 30초 상향
            d = res.json()
            if d.get("status") == "000" and d.get("list"):
                data = d; break
        if not data: return {}

        items = data.get("list", [])
        found = {"equity": None, "total_debt": None, "net_income": None, "op_profit": None, "op_profit_prev": None}
        for item in items:
            acct = item.get("account_nm", "").strip()
            cur = to_int(item.get("thstrm_amount"))
            prev = to_int(item.get("frmtrm_amount"))
            if found["equity"] is None and acct in ["자본총계", "자본 합계"]: found["equity"] = cur
            if found["total_debt"] is None and acct in ["부채총계", "부채 합계"]: found["total_debt"] = cur
            if found["net_income"] is None and acct in ["당기순이익(손실)", "당기순이익", "분기순이익(손실)", "분기순이익"]: found["net_income"] = cur
            if found["op_profit"] is None and acct in ["영업이익(손실)", "영업이익"]: found["op_profit"] = cur; found["op_profit_prev"] = prev

        eq, ni, td, op, opp = found["equity"], found["net_income"], found["total_debt"], found["op_profit"], found["op_profit_prev"]
        if eq is not None: result["equity"] = eq
        if td is not None: result["total_debt"] = td
        if ni is not None: result["net_income"] = ni
        if op is not None and opp and abs(opp) > 0: result["op_growth"] = round((op - opp) / abs(opp) * 100, 2)
        else: result["op_growth"] = 0
        if eq and abs(eq) > 0 and ni is not None: result["roe"] = round(ni / eq * 100, 2)
        if eq and abs(eq) > 0 and td is not None: result["debt_ratio"] = round(td / eq * 100, 2)
        return result if len(result) > 1 else {}
    except Exception as e:
        print(f"❌ {stock_code} 재무데이터 조회 실패: {e}")  # ✅ 오류 종목 로깅 추가        return {}

def run():
    print("[DART START]")
    try: key = get_dart_key()
    except Exception as e: print(f"⛔ {e} → 스킵"); return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    year, quarter = get_latest_quarter()
    reprt_code = {"1Q":"11013","2Q":"11012","3Q":"11014","4Q":"11011"}.get(quarter, "11011")

    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f: ex = json.load(f)
            if ex.get("year") == year and ex.get("quarter") == quarter and ex.get("count", 0) >= 10:
                print(f"ℹ️ {year} {quarter} 이미 수집됨 → 스킵"); return
        except: pass

    target = []
    try:
        with open(INPUT_CSV, "r", encoding="utf-8-sig") as f:  # ✅ 인코딩 통일
            target = [row["code"].zfill(6) for row in csv.DictReader(f)][:TARGET_SIZE]
    except Exception as e:
        print(f"⚠️ {INPUT_CSV} 로드 실패: {e}"); return
    if not target: print("⚠️ 대상 종목 없음 → 스킵"); return

    corp_map = get_corp_codes(key)
    if not corp_map: print("⚠️ corp_map 없음 → 스킵"); return

    print(f"🎯 기준: {year} {quarter} (reprt_code={reprt_code}) | 대상: {len(target)}종목")

    results, skip_cnt, error_cnt = [], 0, 0
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f:
                ex = json.load(f)
                if ex.get("year") == year and ex.get("quarter") == quarter:
                    existing = {s["code"]: s for s in ex.get("stocks", [])}
        except: pass

    for i, code in enumerate(target):
        if code in existing and existing[code].get("reprt_code") == reprt_code:
            skip_cnt += 1; results.append(existing[code]); continue
        corp_code = corp_map.get(code)
        if not corp_code: skip_cnt += 1; continue

        data = fetch_financial(key, corp_code, code, year, reprt_code)
        if data:
            data["reprt_code"] = reprt_code            results.append(data)
        else: error_cnt += 1

        if (i + 1) % 20 == 0: print(f"⏳ {i+1}/{len(target)} 처리중... 성공={len(results)} 실패={error_cnt}")
        time.sleep(SLEEP_SEC)

    output = {"date": today, "year": year, "quarter": quarter, "count": len(results), "skip": skip_cnt, "errors": error_cnt, "stocks": results}
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig") as f: json.dump(output, f, ensure_ascii=False, indent=2)  # ✅ 인코딩 통일
    print(f"[DART DONE] 성공={len(results)} 실패={error_cnt} 스킵={skip_cnt} → {OUTPUT_PATH}")

if __name__ == "__main__": run()
