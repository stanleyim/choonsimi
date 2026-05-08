import os
import json
import pandas as pd
from datetime import datetime
from opendartreader import OpenDartReader as _DartClass

DART_API_KEY = os.getenv("DART_API_KEY")
if not DART_API_KEY:
    raise RuntimeError("DART_API_KEY 환경변수 필요")

HISTORY_CSV = "history.csv"
FUNDAMENTAL_JSON = "fundamental.json"
CORP_MAP_CACHE = "corp_map_cache.json"
TOP_N = 600

dart = _DartClass(DART_API_KEY)

def get_corp_codes():
    """corp_code 캐시 로드 또는 DART에서 다운로드"""
    if os.path.exists(CORP_MAP_CACHE):
        with open(CORP_MAP_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)

    corp_map = dart.list_corp_code()
    with open(CORP_MAP_CACHE, "w", encoding="utf-8") as f:
        json.dump(corp_map, f, ensure_ascii=False)
    return corp_map

def get_quarter_report_code(month, day):
    """현재 날짜 기준 분기 보고서 코드 반환"""
    if month <= 3 and day <= 15:
        return "11013" # 4Q 전년
    elif month <= 5 and day <= 15:
        return "11012" # 1Q
    elif month <= 8 and day <= 14:
        return "11014" # 2Q
    elif month <= 11 and day <= 14:
        return "11015" # 3Q
    else:
        return "11011" # 4Q 당해

def get_financial_data(corp_code, reprt_code, year):
    """CFS 우선, 없으면 OFS에서 재무데이터 추출"""
    try:
        fs = dart.finstate_all(corp_code, year, reprt_code=reprt_code)
        if fs is None or fs.empty:
            fs = dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div="OFS")

        if fs is None or fs.empty:
            return None

        fs = fs[fs["account_nm"].isin(["자본총계", "부채총계", "당기순이익", "영업이익"])]
        if fs.empty:
            return None

        data = {}
        for _, row in fs.iterrows():
            acc = row["account_nm"]
            val = row["amount"]
            if val and str(val).replace("-", "").isdigit():
                data[acc] = int(val)

        return data
    except Exception:
        return None

def main():
    if not os.path.exists(HISTORY_CSV):
        print(f"[ERROR] {HISTORY_CSV} 없음. run.yml 먼저 실행 필요")
        return

    df_hist = pd.read_csv(HISTORY_CSV)
    codes = df_hist["code"].dropna().astype(str).str.zfill(6).unique()[:TOP_N].tolist()
    print(f"[DART] 대상 종목: {len(codes)}개")

    corp_map = get_corp_codes()
    corp_code_dict = {v: k for k, v in corp_map.items()}

    today = datetime.now()
    year = today.year
    reprt_code = get_quarter_report_code(today.month, today.day)
    prev_year = year - 1

    result = {}
    processed = 0
    skipped = 0

    for code in codes:
        corp_code = corp_code_dict.get(code)
        if not corp_code:
            continue

        # 동일 분기 데이터 있으면 스킵
        if os.path.exists(FUNDAMENTAL_JSON):
            with open(FUNDAMENTAL_JSON, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if code in existing and existing[code].get("reprt_code") == reprt_code:
                    skipped += 1
                    continue

        # 현재 분기 데이터
        curr = get_financial_data(corp_code, reprt_code, year)
        # 전기 데이터
        prev = get_financial_data(corp_code, reprt_code, prev_year)

        if not curr or "자본총계" not in curr or curr["자본총계"] == 0:
            continue

        equity = curr["자본총계"]
        debt = curr.get("부채총계", 0)
        net_income = curr.get("당기순이익", 0)
        op_income = curr.get("영업이익", 0)
        prev_op_income = prev.get("영업이익", 0) if prev else 0

        # 지표 계산 및 제한
        roe = (net_income / equity) * 100 if equity > 0 else 0
        roe = max(-50, min(50, roe))

        debt_ratio = (debt / equity) * 100 if equity > 0 else 0
        debt_ratio = max(0, min(500, debt_ratio))

        op_growth = 0
        if prev_op_income and abs(prev_op_income) > 0:
            op_growth = ((op_income - prev_op_income) / abs(prev_op_income)) * 100
            op_growth = max(-100, min(100, op_growth))

        result[code] = {
            "roe": round(roe, 2),
            "debt_ratio": round(debt_ratio, 2),
            "op_income_growth": round(op_growth, 2),
            "year": year,
            "reprt_code": reprt_code
        }
        processed += 1

    with open(FUNDAMENTAL_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {processed}종목 업데이트, {skipped}종목 스킵 → {FUNDAMENTAL_JSON}")

if __name__ == "__main__":
    main()
