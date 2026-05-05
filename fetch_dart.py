"""
fetch_dart.py — v1.0
DART API → 종목별 재무데이터 → fundamental.json
분기별 수집 (오늘 이미 수집했으면 skip)
"""

import os
import json
import time
import zipfile
import io
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

DART_BASE    = "https://opendart.fss.or.kr/api"
OUTPUT_PATH  = "fundamental.json"
KST          = timezone(timedelta(hours=9))


def get_dart_key() -> str:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise RuntimeError("DART_API_KEY 없음")
    return key


def get_corp_codes(key: str) -> dict:
    """stock_code → corp_code 매핑"""
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
            corp_code  = item.findtext("corp_code",  "").strip()
            if stock_code and len(stock_code) == 6:
                corp_map[stock_code] = corp_code

        print(f"[DART] corp_code 매핑: {len(corp_map)}종목")
        return corp_map
    except Exception as e:
        print(f"[DART] corp_code 실패: {e}")
        return {}


def get_latest_quarter() -> tuple:
    now = datetime.now(KST)
    y, m = now.year, now.month
    if m <= 3:  return y - 1, "4Q"
    if m <= 6:  return y,     "1Q"
    if m <= 9:  return y,     "2Q"
    return y, "3Q"


def get_reprt_code(quarter: str) -> str:
    return {"1Q": "11013", "2Q": "11012", "3Q": "11014", "4Q": "11011"}.get(quarter, "11011")


def fetch_financial(key: str, corp_code: str, stock_code: str, year: int, reprt_code: str) -> dict:
    result = {"code": stock_code}
    try:
        for fs_div in ["CFS", "OFS"]:
            res = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={"crtfc_key": key, "corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": reprt_code,
                        "fs_div": fs_div},
                timeout=15
            )
            data = res.json()
            if data.get("status") == "000":
                break
        else:
            return {}

        for item in data.get("list", []):
            acct = item.get("account_nm", "")
            def to_int(v):
                try: return int(str(v or "0").replace(",", "") or 0)
                except: return 0

            cur  = to_int(item.get("thstrm_amount"))
            prev = to_int(item.get("frmtrm_amount"))

            if "영업이익" in acct and "률" not in acct:
                result["op_growth"] = round((cur - prev) / abs(prev) * 100, 2) if prev else 0
            if acct == "자본총계":
                result["equity"] = cur
            if "당기순이익" in acct and "지배" not in acct:
                result["net_income"] = cur
            if acct == "부채총계":
                result["total_debt"] = cur

        if result.get("net_income") and result.get("equity", 0):
            result["roe"] = round(result["net_income"] / result["equity"] * 100, 2)
        if result.get("total_debt") and result.get("equity", 0):
            result["debt_ratio"] = round(result["total_debt"] / result["equity"] * 100, 2)

        return result
    except:
        return {}


def run():
    print("[DART START]")

    try:
        key = get_dart_key()
    except Exception as e:
        print(f"[DART] {e} → skip")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 오늘 이미 수집했으면 skip
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                ex = json.load(f)
            if ex.get("date") == today:
                print("[DART] 오늘 이미 수집 → skip")
                return
        except:
            pass

    # 거래량 상위 200종목
    target = []
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = sorted(raw.get("all", []), key=lambda x: int(x.get("volume", 0)), reverse=True)
        target = [str(i["code"]).zfill(6) for i in items[:200]]
    except Exception as e:
        print(f"[DART] data.json 로드 실패: {e}"); return

    corp_map = get_corp_codes(key)
    if not corp_map:
        print("[DART] corp_map 없음 → skip"); return

    year, quarter = get_latest_quarter()
    reprt_code    = get_reprt_code(quarter)
    print(f"[DART] 기준: {year} {quarter}")

    results = []
    for code in target:
        corp_code = corp_map.get(code)
        if not corp_code:
            continue
        data = fetch_financial(key, corp_code, code, year, reprt_code)
        if data:
            results.append(data)
        time.sleep(0.3)

    output = {"date": today, "year": year, "quarter": quarter,
              "count": len(results), "stocks": results}

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DART DONE] {len(results)}종목 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
