import os
import json
import time
import requests
import shutil
from datetime import datetime, timezone, timedelta

# =========================
# CONFIG
# =========================
KRX_API_KEY = os.getenv("KRX_API_KEY")
OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

KRX_URL = "https://apis.data.go.kr/1160100/service/GetItemInfoService/getItemAll"

# =========================
# UTIL
# =========================
def safe_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0

# =========================
# KRX TOP 200
# =========================
def get_top200():
    try:
        params = {
            "market": "ALL",
            "apiKey": KRX_API_KEY,
            "resultType": "json"
        }
        r = requests.get(KRX_URL, params=params, timeout=20)
        r.raise_for_status()
        stocks = r.json().get("output", [])

        cleaned = []
        for s in stocks:
            mkp = safe_int(s.get("mkp"))
            if mkp > 0:
                cleaned.append((s["srtnCd"], s["itmsNm"], mkp))

        cleaned.sort(key=lambda x: x[2], reverse=True)
        result = [(c, n, m) for c, n, m in cleaned[:200]]
        print(f"[KRX] {len(result)}개 종목 로드 완료")
        return result
    except Exception as e:
        print(f"[ERROR] KRX: {e}")
        return []

# =========================
# MAIN PIPELINE - KRX ONLY
# =========================
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

    # 안전 백업
    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] KRX 종목 {len(output['all'])}개 저장 완료")
    print(f"[INFO] DART 키 등록하면 실제 choonsimi 분석이 활성화됩니다")

if __name__ == "__main__":
    main()
