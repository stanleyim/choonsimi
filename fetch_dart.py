"""
fetch_dart.py — v2.1
history.csv 200종목 → DART 재무데이터 → fundamental.json
"""
import os
import json
import time
import csv
import zipfile
import io
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

DART_BASE = "https://opendart.fss.or.kr/api"
OUTPUT_PATH = "fundamental.json"
CORP_CACHE = "corp_map_cache.json"
INPUT_CSV = "history.csv" # ← history.csv로 수정 완료
KST = timezone(timedelta(hours=9))
TARGET_SIZE = 200
SLEEP_SEC = 0.35

def to_int(v) -> int:
    try:
        return int(str(v or "0").replace(",", "").strip() or "0")
    except Exception:
        return 0

def get_dart_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise RuntimeError("DART_API_KEY 없음")
    return key

def get_corp_codes(key: str) -> dict:
    if os.path.exists(CORP_CACHE):
        try:
            with open(CORP_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print(f" corp_code 캐시 사용: {len(cached)}종목")
            return cached
        except Exception:
            pass

    try:
        res = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": key},
            timeout=30
        )
        res.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        corp_map = {}
        for item in root.findall("list"):
            stock_code = item.findtext("stock_code", "").strip()
            corp_code = item.findtext("corp_code", "").strip()
            if stock_code and len(stock_code) == 6:
                corp_map[stock_code] = corp_code

        with open(CORP_CACHE, "w", encoding="utf-8") as f:
            json.dump(corp_map, f, ensure_ascii=False)

        print(f" corp_code 매핑: {len(corp_map)}종목")
        return corp_map
    except Exception as e:
        print(f" corp_code 실패: {e}")
        return {}

def get_latest_quarter() -> tuple:
    now = datetime.now(KST)
    y, m = now.year, now.month
    if m <= 3: return y - 1, "4Q"
    if m <= 6: return y, "1Q"
    if m <= 9: return y, "2Q"
    return y, "3Q"

def get_reprt_code(quarter: str) -> str:
    return {"1Q": "11013", "2Q": "11012", "3Q": "11014", "4Q": "11011"}.get(quarter, "11011")

def fetch_financial(key: str, corp_code: str, stock_code: str,
                    year: int, reprt_code: str) -> dict:
    result = {"code": stock_code}
    try:
        data = None
        for fs_div in ["CFS", "OFS"]:
            res = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": key,
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt_code,
                    "fs_div": fs_div
                },
                timeout=15
            )
            d = res.json()
            if d.get("status") == "000" and d.get("list"):
                data = d
                break

        if not data:
            return {}

        items = data.get("list", [])
        found = {"equity": None, "total_debt": None, "net_income": None, "op_profit": None, "op_profit_prev": None}

        EQUITY_NAMES = ["자본총계", "자본 합계"]
        DEBT_NAMES = ["부채총계", "부채 합계"]
        NETINC_NAMES = ["당기순이익(손실)", "당기순이익", "분기순이익(손실)", "분기순이익"]
        OPINC_NAMES = ["영업이익(손실)", "영업이익"]

        for item in items:
            acct = item.get("account_nm", "").strip()
            cur = to_int(item.get("thstrm_amount"))
            prev = to_int(item.get("frmtrm_amount"))

            if found["equity"] is None and acct in EQUITY_NAMES:
                found["equity"] = cur
            if found["total_debt"] is None and acct in DEBT_NAMES:
                found["total_debt"] = cur
            if found["net_income"] is None and acct in NETINC_NAMES:
                found["net_income"] = cur
            if found["op_profit"] is None and acct in OPINC_NAMES:
                found["op_profit"] = cur
                found["op_profit_prev"] = prev

        eq, ni, td, op, opp = found["equity"], found["net_income"], found["total_debt"], found["op_profit"], found["op_profit_prev"]

        if eq is not None: result["equity"] = eq
        if td is not None: result["total_debt"] = td
        if ni is not None: result["net_income"] = ni

        if op is not None and opp and abs(opp) > 0:
            result["op_growth"] = round((op - opp) / abs(opp) * 100, 2)
        else:
            result["op_growth"] = 0

        if eq and abs(eq) > 0 and ni is not None:
            roe = round(ni / eq * 100, 2)
            if -500 <= roe <= 500:
                result["roe"] = roe

        if eq and abs(eq) > 0 and td is not None:
            dr = round(td / eq * 100, 2)
            if 0 <= dr <= 5000:
                result["debt_ratio"] = dr

        return result if len(result) > 1 else {}

    except Exception as e:
        print(f" {stock_code} 오류: {e}")
        return {}

def run():
    print("[DART START]")

    try:
        key = get_dart_key()
    except Exception as e:
        print(f" {e} → skip")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    year, quarter = get_latest_quarter()
    reprt_code = get_reprt_code(quarter)

    # 주별 skip
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                ex = json.load(f)
            if ex.get("year") == year and ex.get("quarter") == quarter and ex.get("count", 0) >= 10:
                print(f" {year} {quarter} 이미 수집됨 ({ex['count']}종목) → skip")
                return
        except Exception:
            pass

    # 대상 종목: history.csv 200종목
    target = []
    try:
        with open(INPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            target = [row["code"].zfill(6) for row in reader][:TARGET_SIZE]
        print(f" 대상 종목: {len(target)}개")
    except Exception as e:
        print(f" {INPUT_CSV} 로드 실패: {e}")
        return

    if not target:
        print(" 대상 종목 없음 → skip")
        return

    corp_map = get_corp_codes(key)
    if not corp_map:
        print(" corp_map 없음 → skip")
        return

    print(f" 기준: {year} {quarter} (reprt_code={reprt_code})")

    # 수집
    results, skip_cnt, error_cnt = [], 0, 0
    existing = {}

    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                ex = json.load(f)
            if ex.get("year") == year and ex.get("quarter") == quarter:
                existing = {s["code"]: s for s in ex.get("stocks", [])}
                print(f" 기존 데이터 {len(existing)}종목 로드")
        except Exception:
            pass

    for i, code in enumerate(target):
        if code in existing and existing[code].get("reprt_code") == reprt_code:
            skip_cnt += 1
            results.append(existing[code])
            continue

        corp_code = corp_map.get(code)
        if not corp_code:
            skip_cnt += 1
            continue

        data = fetch_financial(key, corp_code, code, year, reprt_code)
        if data:
            data["reprt_code"] = reprt_code
            results.append(data)
        else:
            error_cnt += 1

        if (i + 1) % 20 == 0:
            print(f" {i+1}/{len(target)} 처리중... 성공={len(results)} 실패={error_cnt}")

        time.sleep(SLEEP_SEC)

    # 저장
    output = {
        "date": today,
        "year": year,
        "quarter": quarter,
        "count": len(results),
        "skip": skip_cnt,
        "errors": error_cnt,
        "stocks": results
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DART DONE] 성공={len(results)} 실패={error_cnt} skip={skip_cnt} → {OUTPUT_PATH}")

if __name__ == "__main__":
    run()
