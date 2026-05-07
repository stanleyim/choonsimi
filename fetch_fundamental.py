"""
fetch_fundamental.py — v2.1
────────────────────────────────────────────────────────────
history.csv 거래대금 상위 600종목 기반 DART 펀더멘털 수집
출력: fundamental.json

v2.0 대비 변경:
  - pykrx 전체종목 → history.csv 거래대금 상위 600 (Top10 포함 보장)
  - 안전 장치: fundamental.json 없어도 engine.py crash 없음

필요: pip install OpenDartReader pykrx pandas requests
환경변수: DART_API_KEY
────────────────────────────────────────────────────────────
"""

import json
import os
import time
import pandas as pd
from datetime import datetime, timezone, timedelta

try:
    import OpenDartReader
except ImportError:
    OpenDartReader = None

OUTPUT   = "fundamental.json"
CORP_CACHE = "corp_map_cache.json"
MAX_STOCKS = 600
SLEEP_SEC  = 0.35

KST = timezone(timedelta(hours=9))

REPRT_MAP = {
    "1Q": "11013",
    "2Q": "11012",
    "3Q": "11014",
    "4Q": "11011",
}

# 클리핑 범위 (engine.py와 동일 기준)
ROE_MIN, ROE_MAX       = -50.0,   50.0
DEBT_MIN, DEBT_MAX     =   0.0,  500.0
GROWTH_MIN, GROWTH_MAX = -100.0, 100.0


# ─────────────────────────── 유틸 ───────────────────────────

def safe_float(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip()) if v else default
    except Exception:
        return default


def to_int(v) -> int:
    try:
        return int(str(v or "0").replace(",", "").strip() or "0")
    except Exception:
        return 0


def get_quarter() -> tuple:
    now = datetime.now(KST)
    y, m = now.year, now.month
    if m <= 3:   return y - 1, "4Q"
    elif m <= 6: return y,     "1Q"
    elif m <= 9: return y,     "2Q"
    else:        return y,     "3Q"


# ─────────────────────── 종목 코드 수집 ─────────────────────

def fetch_stock_codes() -> list:
    """
    history.csv 거래대금 상위 MAX_STOCKS 종목 코드 반환
    → Top10이 fundamental.json에 100% 포함되도록 보장
    """
    try:
        df = pd.read_csv("history.csv", dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)

        # 오늘 날짜 필터 (history.csv가 누적인 경우 대비)
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        if "date" in df.columns:
            df_today = df[df["date"] == today_str]
            if df_today.empty:
                df_today = df  # fallback: 전체 사용
        else:
            df_today = df

        # 거래량 기준 정렬 → 상위 MAX_STOCKS
        if "volume" in df_today.columns:
            df_sorted = df_today.sort_values("volume", ascending=False)
        else:
            df_sorted = df_today

        codes = df_sorted["code"].head(MAX_STOCKS).tolist()
        print(f"[FUND] history.csv → {len(codes)}종목 선택")
        return codes

    except FileNotFoundError:
        print("[WARN] history.csv 없음 → 빈 리스트")
        return []
    except Exception as e:
        print(f"[WARN] history.csv 읽기 실패: {e} → 빈 리스트")
        return []


# ─────────────────────── corp_code 매핑 ─────────────────────

def get_corp_map(dart) -> dict:
    """DART corp_code 매핑 (캐시 우선)"""
    import zipfile, io, requests
    import xml.etree.ElementTree as ET

    try:
        res = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": dart.api_key},
            timeout=30,
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

        with open(CORP_CACHE, "w", encoding="utf-8") as f:
            json.dump(corp_map, f, ensure_ascii=False)
        print(f"[FUND] corp_map {len(corp_map)}종목 갱신")
        return corp_map

    except Exception as e:
        print(f"[WARN] corp_map 갱신 실패: {e} → 캐시 사용")
        try:
            with open(CORP_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            print(f"[FUND] corp_map 캐시 {len(cached)}종목")
            return cached
        except Exception:
            return {}


# ──────────────────────── 재무 파싱 ─────────────────────────

EQUITY_NAMES    = ["자본총계", "자본 합계"]
DEBT_NAMES      = ["부채총계", "부채 합계"]
NETINC_NAMES    = ["당기순이익(손실)", "당기순이익", "분기순이익(손실)", "분기순이익"]
OPINC_NAMES     = ["영업이익(손실)", "영업이익"]


def parse_statement(rows: list) -> dict:
    result = {
        "equity":         None,
        "total_debt":     None,
        "op_income":      None,
        "prev_op_income": None,
        "net_income":     None,
    }
    for row in rows:
        acct = str(row.get("account_nm", "")).strip()
        cur  = to_int(row.get("thstrm_amount"))
        prev = to_int(row.get("frmtrm_amount"))

        if result["equity"]     is None and acct in EQUITY_NAMES:
            result["equity"] = cur
        if result["total_debt"] is None and acct in DEBT_NAMES:
            result["total_debt"] = cur
        if result["net_income"] is None and acct in NETINC_NAMES:
            result["net_income"] = cur
        if result["op_income"]  is None and acct in OPINC_NAMES:
            result["op_income"]      = cur
            result["prev_op_income"] = prev
    return result


def calc_metrics(d: dict, code: str) -> dict:
    equity   = d["equity"]   or 0
    tot_debt = d["total_debt"] or 0
    op       = d["op_income"]  or 0
    prev_op  = d["prev_op_income"] or 0
    net_inc  = d["net_income"] or 0

    # ROE
    roe = 0.0
    if equity != 0:
        raw = (net_inc / equity) * 100
        roe = round(min(ROE_MAX, max(ROE_MIN, raw)), 2)

    # 부채비율
    debt_ratio = 0.0
    if equity > 0:
        raw = (tot_debt / equity) * 100
        debt_ratio = round(min(DEBT_MAX, max(DEBT_MIN, raw)), 2)

    # 영업이익 성장률
    op_growth = 0.0
    if prev_op != 0:
        raw = ((op - prev_op) / abs(prev_op)) * 100
        op_growth = round(min(GROWTH_MAX, max(GROWTH_MIN, raw)), 2)

    return {
        "code":       code,
        "equity":     int(equity),
        "total_debt": int(tot_debt),
        "op_growth":  op_growth,
        "net_income": int(net_inc),
        "roe":        roe,
        "debt_ratio": debt_ratio,
    }


def fetch_finstate(dart, corp_code: str, year: int, reprt_code: str) -> list:
    """연결 → 개별 순서로 재무제표 조회"""
    for fs_div in ("CFS", "OFS"):
        try:
            df = dart.finstate_all(corp_code, year,
                                   reprt_code=reprt_code, fs_div=fs_div)
            if df is not None and not df.empty:
                return df.to_dict("records")
        except Exception:
            pass
        time.sleep(0.1)
    return []


# ──────────────────────────── 메인 ──────────────────────────

def main():
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] DART_API_KEY 환경변수 없음")
        _save_empty()
        return

    if OpenDartReader is None:
        print("[ERROR] OpenDartReader 미설치")
        _save_empty()
        return

    dart           = OpenDartReader.OpenDartReader(api_key)
    year, quarter  = get_quarter()
    reprt_code     = REPRT_MAP[quarter]
    codes          = fetch_stock_codes()

    if not codes:
        print("[WARN] 대상 종목 없음")
        _save_empty(year, quarter)
        return

    # ── 이미 수집 여부 체크 ──────────────────────────────────
    if os.path.exists(OUTPUT):
        try:
            with open(OUTPUT, "r", encoding="utf-8") as f:
                ex = json.load(f)
            if (ex.get("year") == year and
                    ex.get("quarter") == quarter and
                    ex.get("count", 0) >= 10):
                print(f"[FUND] {year} {quarter} 이미 수집됨 ({ex['count']}종목) → skip")
                return
        except Exception:
            pass

    corp_map = get_corp_map(dart)
    if not corp_map:
        print("[WARN] corp_map 없음 → skip")
        _save_empty(year, quarter)
        return

    print(f"[FUND] {year} {quarter} ({reprt_code}) 수집 시작 — {len(codes)}종목")

    results, fail_cnt = [], 0

    for i, code in enumerate(codes, 1):
        corp_code = corp_map.get(code)
        if not corp_code:
            fail_cnt += 1
            time.sleep(SLEEP_SEC)
            continue
        try:
            rows = fetch_finstate(dart, corp_code, year, reprt_code)
            if rows:
                raw     = parse_statement(rows)
                metrics = calc_metrics(raw, code)
                results.append(metrics)
            else:
                fail_cnt += 1
        except Exception as e:
            fail_cnt += 1

        time.sleep(SLEEP_SEC)

        if i % 100 == 0:
            print(f"  [{i}/{len(codes)}] 성공={len(results)} 실패={fail_cnt}")

    _save(results, year, quarter)
    print(f"[DONE] fundamental.json — {len(results)}종목 / 실패 {fail_cnt}개")


def _save(results: list, year: int = 0, quarter: str = ""):
    output = {
        "date":       datetime.now(KST).strftime("%Y-%m-%d"),
        "year":       year,
        "quarter":    quarter,
        "count":      len(results),
        "stocks":     results,
        "updated_at": datetime.now(KST).isoformat(),
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def _save_empty(year: int = 0, quarter: str = ""):
    _save([], year, quarter)
    print("[WARN] 빈 fundamental.json 저장 (engine.py crash 방지)")


if __name__ == "__main__":
    main()
