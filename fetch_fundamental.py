"""
fetch_fundamental.py — DART API 기반 분기 펀더멘털 수집
────────────────────────────────────────────────────────
출력: fundamental.json
구조: { date, year, quarter, count, stocks: [
          { code, equity, total_debt, op_growth,
            net_income, roe, debt_ratio }
       ]}

필요 패키지:
  pip install OpenDartReader pykrx requests

환경변수:
  DART_API_KEY  (GitHub Secrets → Actions env)
────────────────────────────────────────────────────────
"""

import json
import os
import time
from datetime import datetime

try:
    import OpenDartReader
except ImportError:
    OpenDartReader = None

try:
    from pykrx import stock as krx
except ImportError:
    krx = None

OUTPUT   = "fundamental.json"
MAX_STOCKS = 600    # KOSPI+KOSDAQ 상위 종목 수 제한
SLEEP_SEC  = 0.35   # DART API rate limit 방지

# 분기 코드 매핑
REPRT_MAP = {
    "1Q": "11013",
    "2Q": "11012",
    "3Q": "11014",
    "4Q": "11011",
}

# ROE / 부채비율 클리핑 범위
ROE_MIN, ROE_MAX         = -999.0,  999.0
DEBT_MIN, DEBT_MAX       =    0.0, 9999.0
GROWTH_MIN, GROWTH_MAX   = -100.0, 1000.0


# ───────────────────────────── 유틸 ─────────────────────────────

def safe_float(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip()) if v else default
    except Exception:
        return default


def get_quarter() -> tuple:
    """현재 날짜 기준 최신 분기 반환 (year, quarter_str)"""
    now = datetime.now()
    y, m = now.year, now.month
    if m <= 3:
        return y - 1, "4Q"
    elif m <= 6:
        return y, "1Q"
    elif m <= 9:
        return y, "2Q"
    else:
        return y, "3Q"


# ─────────────────────────── 종목 목록 ──────────────────────────

def fetch_stock_codes() -> list:
    """pykrx로 KOSPI + KOSDAQ 전체 종목 코드 리스트 반환"""
    if krx is None:
        print("[WARN] pykrx 없음 → 빈 리스트")
        return []
    today = datetime.now().strftime("%Y%m%d")
    try:
        kospi  = krx.get_market_ticker_list(today, market="KOSPI")
        kosdaq = krx.get_market_ticker_list(today, market="KOSDAQ")
        codes  = list(dict.fromkeys(list(kospi) + list(kosdaq)))  # 중복 제거, 순서 유지
        print(f"[DATA] KOSPI {len(kospi)} + KOSDAQ {len(kosdaq)} = {len(codes)} 종목")
        return codes[:MAX_STOCKS]
    except Exception as e:
        print(f"[ERROR] 종목 목록 조회 실패: {e}")
        return []


# ──────────────────────────── 재무 파싱 ─────────────────────────

ACCOUNT_MAP = {
    "자본총계":   "equity",
    "부채총계":   "total_debt",
    "영업이익":   "op_income",
    "당기순이익": "net_income",
}


def parse_statement(rows: list) -> dict:
    """
    DART 재무제표 rows 리스트 → 필요 수치 추출
    thstrm_amount = 당기, frmtrm_amount = 전기
    """
    result = {
        "equity":         0.0,
        "total_debt":     0.0,
        "op_income":      0.0,
        "prev_op_income": 0.0,
        "net_income":     0.0,
    }

    for row in rows:
        acnt = str(row.get("account_nm", ""))
        for key, field in ACCOUNT_MAP.items():
            if key in acnt:
                cur  = safe_float(row.get("thstrm_amount"))
                prev = safe_float(row.get("frmtrm_amount"))
                if field == "op_income":
                    result["op_income"]      = cur
                    result["prev_op_income"] = prev
                elif result[field] == 0.0:     # 첫 번째 매칭만 사용
                    result[field] = cur
    return result


def calc_metrics(d: dict, code: str) -> dict:
    """추출 수치 → 비율 지표 계산 (클리핑 포함)"""
    equity    = d["equity"]
    tot_debt  = d["total_debt"]
    op        = d["op_income"]
    prev_op   = d["prev_op_income"]
    net_inc   = d["net_income"]

    # ROE (%)
    roe = 0.0
    if equity != 0:
        raw_roe = (net_inc / equity) * 100
        roe = round(min(ROE_MAX, max(ROE_MIN, raw_roe)), 2)

    # 부채비율 (%)
    debt_ratio = 0.0
    if equity > 0:
        raw_dr = (tot_debt / equity) * 100
        debt_ratio = round(min(DEBT_MAX, max(DEBT_MIN, raw_dr)), 2)

    # 영업이익 성장률 (%)
    op_growth = 0.0
    if prev_op != 0:
        raw_gr = ((op - prev_op) / abs(prev_op)) * 100
        op_growth = round(min(GROWTH_MAX, max(GROWTH_MIN, raw_gr)), 2)

    return {
        "code":        code,
        "equity":      int(equity),
        "total_debt":  int(tot_debt),
        "op_growth":   op_growth,
        "net_income":  int(net_inc),
        "roe":         roe,
        "debt_ratio":  debt_ratio,
    }


# ──────────────────────────── DART 수집 ─────────────────────────

def fetch_finstate(dart, corp_code: str, year: int, reprt_code: str) -> list:
    """연결 → 개별 순서로 재무제표 조회, rows 반환"""
    for fs_div in ("CFS", "OFS"):
        try:
            df = dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div=fs_div)
            if df is not None and not df.empty:
                return df.to_dict("records")
        except Exception:
            pass
        time.sleep(0.1)
    return []


def main():
    # ── 환경변수 체크 ──
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] DART_API_KEY 환경변수가 설정되지 않았습니다.")
        # 빈 파일이라도 저장해서 engine.py가 crash 안 나게
        _save_empty()
        return

    if OpenDartReader is None:
        print("[ERROR] OpenDartReader 패키지가 설치되지 않았습니다.")
        _save_empty()
        return

    dart            = OpenDartReader.OpenDartReader(api_key)
    year, quarter   = get_quarter()
    reprt_code      = REPRT_MAP[quarter]
    codes           = fetch_stock_codes()

    if not codes:
        print("[WARN] 종목 목록 비어있음")
        _save_empty(year, quarter)
        return

    print(f"[FUND] {year} {quarter} ({reprt_code}) 수집 시작 — 대상 {len(codes)}종목")

    results, fail_cnt = [], 0

    for i, code in enumerate(codes, 1):
        try:
            corp_code = dart.find_corp_code(code)
            if not corp_code:
                fail_cnt += 1
                continue

            rows = fetch_finstate(dart, corp_code, year, reprt_code)
            if not rows:
                fail_cnt += 1
                continue

            raw     = parse_statement(rows)
            metrics = calc_metrics(raw, code)
            results.append(metrics)

        except Exception as e:
            fail_cnt += 1
            # 개별 종목 에러는 무시하고 계속 진행
            pass

        finally:
            time.sleep(SLEEP_SEC)

        if i % 100 == 0:
            print(f"  [{i}/{len(codes)}] 성공={len(results)} 실패={fail_cnt}")

    _save(results, year, quarter)
    print(f"[DONE] fundamental.json — {len(results)}종목 저장 / 실패 {fail_cnt}개")


def _save(results: list, year: int = 0, quarter: str = ""):
    output = {
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "year":       year,
        "quarter":    quarter,
        "count":      len(results),
        "stocks":     results,
        "updated_at": datetime.now().isoformat(),
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def _save_empty(year: int = 0, quarter: str = ""):
    _save([], year, quarter)
    print("[WARN] 빈 fundamental.json 저장 (engine.py crash 방지)")


if __name__ == "__main__":
    main()
