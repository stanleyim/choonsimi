import os
import json
import shutil
import time
from datetime import datetime, timezone, timedelta
import requests
from workalendar.asia import SouthKorea

KRX_API_KEY = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")
OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
DART_BASE = "https://opendart.fss.or.kr/api"

cal = SouthKorea()

# ────────────────────────────────────────
# 유틸
# ────────────────────────────────────────
def safe_int(v):
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except:
        return 0

def safe_float(v):
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except:
        return 0.0

def get_trading_day(kst):
    today = datetime.now(kst).date()
    for i in range(10):
        day = today - timedelta(days=i)
        if cal.is_working_day(day):
            return day.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

# ────────────────────────────────────────
# KRX 수집
# ────────────────────────────────────────
def get_krx_data(url, bas_dd):
    headers = {
        "AUTH_KEY": KRX_API_KEY.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    r = requests.post(url, headers=headers, json={"basDd": bas_dd}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return (
        data.get("OutBlock_1")
        or data.get("block1")
        or data.get("data")
        or []
    )

def get_krx_data_with_fallback(url, bas_dd):
    base_date = datetime.strptime(bas_dd, "%Y%m%d").date()
    endpoint = url.split("/")[-1]
    for i in range(7):
        day = base_date - timedelta(days=i)
        if not cal.is_working_day(day):
            continue
        try:
            date_str = day.strftime("%Y%m%d")
            data = get_krx_data(url, date_str)
            if data:
                print(f"[KRX] {endpoint} {date_str} 성공 ({len(data)}개)")
                return data
            print(f"[KRX] {endpoint} {date_str} 빈 응답")
        except Exception as e:
            print(f"[KRX] {endpoint} {day} 재시도 중... ({type(e).__name__})")
    print(f"[KRX] {endpoint} 데이터 없음")
    return []

def get_top200():
    try:
        kst = timezone(timedelta(hours=9))
        bas_dd = get_trading_day(kst)
        print(f"[KRX] 기준일: {bas_dd}")

        kospi_items = get_krx_data_with_fallback(KOSPI_URL, bas_dd)
        kosdaq_items = get_krx_data_with_fallback(KOSDAQ_URL, bas_dd)
        all_items = kospi_items + kosdaq_items

        if not all_items:
            print("[KRX] 전체 데이터 없음")
            return []

        print(f"[KRX] 총 {len(all_items)}개 수집")

        cleaned = []
        for s in all_items:
            mcap = safe_int(s.get("MKTCAP", 0))
            code = s.get("ISU_CD", "")
            name = s.get("ISU_NM", "")
            if mcap > 0 and name:
                cleaned.append((code, name, mcap))

        cleaned.sort(key=lambda x: x[2], reverse=True)
        result = cleaned[:200]
        print(f"[KRX] TOP {len(result)}개 확정")
        return result

    except Exception as e:
        print(f"[ERROR] get_top200 실패: {type(e).__name__}")
        return []

# ────────────────────────────────────────
# DART 재무데이터
# ────────────────────────────────────────
def get_corp_code(stock_code):
    """종목코드 → DART corp_code 변환"""
    try:
        r = requests.get(
            f"{DART_BASE}/company.json",
            params={"crtfc_key": DART_API_KEY, "stock_code": stock_code},
            timeout=10
        )
        data = r.json()
        if data.get("status") == "000":
            return data.get("corp_code")
    except:
        pass
    return None

def get_financial_data(corp_code, year):
    """재무제표 조회 (사업보고서 기준)"""
    try:
        r = requests.get(
            f"{DART_BASE}/fnlttSinglAcnt.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011" # 사업보고서
            },
            timeout=10
        )
        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])
    except:
        pass
    return []

def extract_financials(fin_list):
    """재무항목 추출 → ROE / 부채비율 / 영업이익"""
    result = {
        "roe": 0.0,
        "debt_ratio": 999.0,
        "op_profit": 0,
        "op_profit_py": 0, # 전년도 영업이익
        "net_income": 0,
        "equity": 0,
    }
    for item in fin_list:
        nm = item.get("account_nm", "")
        val = safe_int(item.get("thstrm_amount", 0))
        py = safe_int(item.get("frmtrm_amount", 0))

        if "영업이익" in nm:
            result["op_profit"] = val
            result["op_profit_py"] = py
        elif "당기순이익" in nm:
            result["net_income"] = val
        elif "자본총계" in nm:
            result["equity"] = val
        elif "부채총계" in nm:
            result["debt_total"] = val

    # ROE 계산
    if result["equity"] > 0:
        result["roe"] = round(result["net_income"] / result["equity"] * 100, 2)

    # 부채비율 계산
    debt = result.get("debt_total", 0)
    equity = result["equity"]
    if equity > 0:
        result["debt_ratio"] = round(debt / equity * 100, 2)

    return result

def get_dart_financials(stock_code):
    """종목코드로 최신 재무데이터 조회"""
    if not DART_API_KEY:
        return None

    # KRX 코드 A005930 → DART 코드 005930 변환이 핵심
    dart_code = stock_code.lstrip("A")
    
    corp_code = get_corp_code(dart_code)
    if not corp_code:
        return None

    # 최근 연도 시도 (올해 → 작년)
    kst = timezone(timedelta(hours=9))
    year = datetime.now(kst).year
    for y in [year - 1, year - 2]:
        fin_list = get_financial_data(corp_code, y)
        if fin_list:
            return extract_financials(fin_list)

    return None

# ────────────────────────────────────────
# 혼합형 스코어링
# ────────────────────────────────────────
def calc_score(mcap, mcap_rank, fin):
    """
    혼합형 AI 스코어 (0~100점)
    - 시가총액 순위: 20%
    - ROE: 20%
    - 부채비율: 20%
    - 영업이익증가율: 20%
    - PER 대용: 20% (시총/순이익)
    """
    if fin is None:
        return 0, "재무데이터 없음"

    score = 0
    reasons = []

    # 1) 시가총액 순위
    rank_score = max(0, 20 - (mcap_rank / 200 * 20))
    score += rank_score

    # 2) ROE (15% 이상 만점)
    roe = fin["roe"]
    if roe >= 15:
        score += 20
        reasons.append(f"ROE {roe:.1f}%↑")
    elif roe >= 10:
        score += 14
        reasons.append(f"ROE {roe:.1f}%")
    elif roe >= 5:
        score += 8
    elif roe > 0:
        score += 3

    # 3) 부채비율
    dr = fin["debt_ratio"]
    if dr <= 50:
        score += 20
        reasons.append(f"부채비율 {dr:.0f}%↓")
    elif dr <= 100:
        score += 14
    elif dr <= 200:
        score += 7
    elif dr <= 300:
        score += 3

    # 4) 영업이익 증가율
    op = fin["op_profit"]
    op_py = fin["op_profit_py"]
    if op_py > 0 and op > 0:
        growth = (op - op_py) / abs(op_py) * 100
        if growth >= 30:
            score += 20
            reasons.append(f"영업이익 +{growth:.0f}%↑")
        elif growth >= 10:
            score += 14
            reasons.append(f"영업이익 +{growth:.0f}%")
        elif growth >= 0:
            score += 8
        else:
            score += 2
    elif op > 0:
        score += 5

    # 5) PER 대용
    net = fin["net_income"]
    if net > 0:
        per = mcap / net
        if per <= 10:
            score += 20
            reasons.append(f"저PER {per:.1f}배")
        elif per <= 15:
            score += 14
        elif per <= 25:
            score += 8
        elif per <= 40:
            score += 3

    reason = ", ".join(reasons) if reasons else "분석 완료"
    return round(score, 1), reason

def classify_signal(score):
    if score >= 75:
        return "STRONG_BUY"
    elif score >= 60:
        return "BUY"
    elif score >= 45:
        return "HOLD"
    elif score >= 30:
        return "WATCH"
    else:
        return "PASS"

# ────────────────────────────────────────
# 메인
# ────────────────────────────────────────
def main():
    print("[START] choonsimi engine (DART 혼합형)")

    tickers = get_top200()
    if not tickers:
        print("[FAIL] KRX 데이터 없음")
        return

    results = []
    total = len(tickers)

    for rank, (code, name, mcap) in enumerate(tickers, 1):
        print(f"[DART] {rank}/{total} {name} ({code}) 분석 중...")

        fin = get_dart_financials(code)
        score, reason = calc_score(mcap, rank, fin)
        signal = classify_signal(score) if fin else "KRX_ONLY"

        results.append({
            "code": code,
            "name": name,
            "market_cap": mcap,
            "signal_strength": score,
            "signal": signal,
            "growth": round(fin["roe"], 2) if fin else 0,
            "debt_ratio": round(fin["debt_ratio"], 2) if fin else 0,
            "reason": reason,
            "confidence": round(score / 100, 2)
        })

        time.sleep(0.2) # DART API 호출 제한 방지

    # 스코어 내림차순 정렬
    results.sort(key=lambda x: x["signal_strength"], reverse=True)

    kst = timezone(timedelta(hours=9))
    version = datetime.now(kst).strftime("%Y%m%d_%H%M")

    output = {
        "version": version,
        "generated_at": datetime.now(kst).isoformat(),
        "top10": results[:10],
        "all": results[:200]
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {len(output['all'])}개 저장 완료")

    # 상위 10개 출력
    print("\n[TOP10 결과]")
    for i, r in enumerate(results[:10], 1):
        print(f"{i:2}. {r['name']} ({r['code']}) | {r['signal']} | 점수: {r['signal_strength']} | {r['reason']}")

if __name__ == "__main__":
    main()
