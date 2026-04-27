import os
import json
import shutil
from datetime import datetime, timezone, timedelta
import requests

KRX_API_KEY = os.getenv("KRX_API_KEY")
OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

# 수정: 올바른 엔드포인트
KRX_URL = "https://apis.data.go.kr/1160100/service/GetStockPriceInfoService/getStockPriceInfo"

def safe_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0

def get_top200():
    try:
        # 수정: serviceKey + 올바른 파라미터
        params = {
            "serviceKey": KRX_API_KEY,
            "resultType": "json",
            "numOfRows": "500", # 200개 이상 받아서 상위 200개 필터링
            "pageNo": "1"
        }
        r = requests.get(KRX_URL, params=params, timeout=30)
        r.raise_for_status()
        items = r.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])

        cleaned = []
        for s in items:
            mkp = safe_int(s.get("mkp")) # 시가총액
            if mkp > 0 and s.get("itmsNm"): # 종목명 있는 것만
                cleaned.append((s["srtnCd"], s["itmsNm"], mkp))

        cleaned.sort(key=lambda x: x[2], reverse=True)
        result = cleaned[:200]
        print(f"[KRX] {len(result)}개 종목 로드 완료")
        return result
    except Exception as e:
        print(f"[ERROR] KRX: {e}")
        return []

def main():
    print("[START] choonsimi KRX test mode")

    tickers = get_top200()
    if not tickers:
        print("[FAIL] KRX 데이터 없음")
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

    print(f"[DONE] KRX 종목 {len(output['all'])}개 저장 완료")

if __name__ == "__main__":
    main()
