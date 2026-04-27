import os
import json
import shutil
from datetime import datetime, timezone, timedelta
import requests
from workalendar.asia import SouthKorea

KRX_API_KEY = os.getenv("KRX_API_KEY")
OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

KRX_BASE   = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL  = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

cal = SouthKorea()

def safe_int(v):
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except:
        return 0

def get_trading_day(kst):
    today = datetime.now(kst).date()
    for i in range(10):
        day = today - timedelta(days=i)
        if cal.is_working_day(day):
            return day.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

def get_krx_data(url, bas_dd):
    headers = {
        "AUTH_KEY": KRX_API_KEY.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"basDd": bas_dd}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
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
        except Exception as e:
            # ✅ API Key 절대 노출 금지
            print(f"[KRX] {endpoint} {day} 재시도 중...")
            continue
    print(f"[KRX] {endpoint} 데이터 없음")
    return []

def get_top200():
    try:
        kst = timezone(timedelta(hours=9))
        bas_dd = get_trading_day(kst)
        print(f"[KRX] 기준일: {bas_dd}")

        kospi_items  = get_krx_data_with_fallback(KOSPI_URL,  bas_dd)
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
        print(f"[ERROR] get_top200 실패")
        return []

def main():
    print("[START] choonsimi engine")

    tickers = get_top200()
    if not tickers:
        print("[FAIL] 데이터 없음")
        return

    results = []
    for code, name, mcap in tickers:
        results.append({
            "code": code,
            "name": name,
            "market_cap": mcap,
            "signal_strength": 0,
            "signal": "KRX_ONLY",
            "growth": 0,
            "reason": "DART 키 등록 후 실제 분석 시작",
            "confidence": 0
        })

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

if __name__ == "__main__":
    main()
