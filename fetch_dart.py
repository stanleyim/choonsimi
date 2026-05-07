"""
fetch_dart.py — v2.0
DART API → 종목별 재무데이터 → fundamental.json

v1.0 대비 수정:
  1. count=1 버그 수정: 일별 skip → 주별 skip (weekly 실행 기준)
  2. ROE/debt_ratio 단위 불일치 수정: 동일 API 호출 내 단위 통일
  3. 계정명 정확 매칭: contains → exact/priority 매칭
  4. 수집 실패 상세 로그 추가
"""

import os
import json
import time
import zipfile
import io
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

DART_BASE   = "https://opendart.fss.or.kr/api"
OUTPUT_PATH = "fundamental.json"
CORP_CACHE  = "corp_map_cache.json"
KST         = timezone(timedelta(hours=9))
TARGET_SIZE = 200   # 거래량 상위 N 종목
SLEEP_SEC   = 0.35  # DART API rate limit 방지


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
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


# ──────────────────────────────────────────────
# corp_code 매핑 (캐시 우선)
# ──────────────────────────────────────────────
def get_corp_codes(key: str) -> dict:
    # 캐시 있으면 재사용 (주 1회 실행이므로 매번 새로 받아도 됨)
    try:
        res = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": key},
            timeout=30
        )
        res.raise_for_status()
        zf       = zipfile.ZipFile(io.BytesIO(res.content))
        xml_data = zf.read("CORPCODE.xml")
        root     = ET.fromstring(xml_data)

        corp_map = {}
        for item in root.findall("list"):
            stock_code = item.findtext("stock_code", "").strip()
            corp_code  = item.findtext("corp_code",  "").strip()
            if stock_code and len(stock_code) == 6:
                corp_map[stock_code] = corp_code

        # 캐시 저장
        with open(CORP_CACHE, "w", encoding="utf-8") as f:
            json.dump(corp_map, f, ensure_ascii=False)

        print(f"[DART] corp_code 매핑: {len(corp_map)}종목")
        return corp_map

    except Exception as e:
        print(f"[DART] corp_code 실패: {e}")
        # 캐시 fallback
        try:
            with open(CORP_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print(f"[DART] corp_code 캐시 사용: {len(cached)}종목")
            return cached
        except Exception:
            return {}


# ──────────────────────────────────────────────
# 분기 계산
# ──────────────────────────────────────────────
def get_latest_quarter() -> tuple:
    now = datetime.now(KST)
    y, m = now.year, now.month
    if m <= 3:  return y - 1, "4Q"
    if m <= 6:  return y,     "1Q"
    if m <= 9:  return y,     "2Q"
    return y, "3Q"


def get_reprt_code(quarter: str) -> str:
    return {"1Q": "11013", "2Q": "11012", "3Q": "11014", "4Q": "11011"}.get(quarter, "11011")


# ──────────────────────────────────────────────
# 재무데이터 수집 (단위 통일 핵심 수정)
# ──────────────────────────────────────────────
def fetch_financial(key: str, corp_code: str, stock_code: str,
                    year: int, reprt_code: str) -> dict:
    result = {"code": stock_code}
    try:
        # CFS(연결) 우선, 없으면 OFS(별도)
        data = None
        for fs_div in ["CFS", "OFS"]:
            res = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": key,
                    "corp_code":  corp_code,
                    "bsns_year":  str(year),
                    "reprt_code": reprt_code,
                    "fs_div":     fs_div
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

        # ── 계정명 우선순위 매칭 (정확도 향상) ──────────────
        # 동일 계정이 여러 행 있을 때 첫 번째(당기) 값만 사용
        found = {
            "equity":     None,
            "total_debt": None,
            "net_income": None,
            "op_profit":  None,
            "op_profit_prev": None,
        }

        # 우선순위 계정명 (앞에 있을수록 우선)
        EQUITY_NAMES    = ["자본총계", "자본 합계"]
        DEBT_NAMES      = ["부채총계", "부채 합계"]
        NETINC_NAMES    = ["당기순이익(손실)", "당기순이익", "분기순이익(손실)", "분기순이익"]
        OPINC_NAMES     = ["영업이익(손실)", "영업이익"]

        for item in items:
            acct = item.get("account_nm", "").strip()
            cur  = to_int(item.get("thstrm_amount"))
            prev = to_int(item.get("frmtrm_amount"))

            if found["equity"] is None and acct in EQUITY_NAMES:
                found["equity"] = cur

            if found["total_debt"] is None and acct in DEBT_NAMES:
                found["total_debt"] = cur

            if found["net_income"] is None and acct in NETINC_NAMES:
                found["net_income"] = cur

            if found["op_profit"] is None and acct in OPINC_NAMES:
                found["op_profit"]      = cur
                found["op_profit_prev"] = prev

        # ── 결과 저장 ──────────────────────────────────────
        if found["equity"] is not None:
            result["equity"] = found["equity"]
        if found["total_debt"] is not None:
            result["total_debt"] = found["total_debt"]
        if found["net_income"] is not None:
            result["net_income"] = found["net_income"]

        # 영업이익 증가율
        op  = found["op_profit"]
        opp = found["op_profit_prev"]
        if op is not None and opp and abs(opp) > 0:
            result["op_growth"] = round((op - opp) / abs(opp) * 100, 2)
        else:
            result["op_growth"] = 0

        # ── ROE / Debt Ratio (같은 단위 내 계산) ────────────
        eq  = found["equity"]
        ni  = found["net_income"]
        td  = found["total_debt"]

        if eq and abs(eq) > 0 and ni is not None:
            roe = round(ni / eq * 100, 2)
            # 비정상 값 필터 (-500 ~ 500% 범위 밖은 제외)
            if -500 <= roe <= 500:
                result["roe"] = roe

        if eq and abs(eq) > 0 and td is not None:
            dr = round(td / eq * 100, 2)
            # 비정상 값 필터 (0 ~ 5000% 범위 밖은 제외)
            if 0 <= dr <= 5000:
                result["debt_ratio"] = dr

        return result if len(result) > 1 else {}

    except Exception as e:
        print(f"[DART] {stock_code} 오류: {e}")
        return {}


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def run():
    print("[DART START]")

    try:
        key = get_dart_key()
    except Exception as e:
        print(f"[DART] {e} → skip")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # ── 주별 skip (같은 주에 이미 수집했으면 skip) ─────────
    # v1.0 버그: "오늘" 수집 여부로 체크 → 1종목만 수집 후 skip 발생
    # v2.0 수정: 같은 year+quarter 기준으로 체크
    year, quarter = get_latest_quarter()

    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                ex = json.load(f)
            if (ex.get("year") == year and
                ex.get("quarter") == quarter and
                ex.get("count", 0) >= 10):   # 10개 미만이면 재수집
                print(f"[DART] {year} {quarter} 이미 수집됨 ({ex['count']}종목) → skip")
                return
        except Exception:
            pass

    # ── 대상 종목: 거래량 상위 200 ────────────────────────
    target = []
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        items  = sorted(raw.get("all", []),
                        key=lambda x: int(x.get("volume", 0)), reverse=True)
        target = [str(i["code"]).zfill(6) for i in items[:TARGET_SIZE]]
        print(f"[DART] 대상 종목: {len(target)}개")
    except Exception as e:
        print(f"[DART] data.json 로드 실패: {e}")
        return

    corp_map = get_corp_codes(key)
    if not corp_map:
        print("[DART] corp_map 없음 → skip")
        return

    reprt_code = get_reprt_code(quarter)
    print(f"[DART] 기준: {year} {quarter} (reprt_code={reprt_code})")

    # ── 수집 ─────────────────────────────────────────────
    results   = []
    skip_cnt  = 0
    error_cnt = 0

    for i, code in enumerate(target):
        corp_code = corp_map.get(code)
        if not corp_code:
            skip_cnt += 1
            continue

        data = fetch_financial(key, corp_code, code, year, reprt_code)

        if data:
            results.append(data)
        else:
            error_cnt += 1

        if (i + 1) % 20 == 0:
            print(f"[DART] {i+1}/{len(target)} 처리중 ... 성공={len(results)} 실패={error_cnt}")

        time.sleep(SLEEP_SEC)

    # ── 저장 ─────────────────────────────────────────────
    output = {
        "date":    today,
        "year":    year,
        "quarter": quarter,
        "count":   len(results),
        "skip":    skip_cnt,
        "errors":  error_cnt,
        "stocks":  results
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DART DONE] 성공={len(results)} 실패={error_cnt} skip={skip_cnt} → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
