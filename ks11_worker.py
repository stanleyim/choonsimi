"""
ks11_worker.py — KS11 전용 수집기
- 5분 캐시
- market_flow.json 업데이트
- fetch_data와 완전 분리
"""

import io
import json
import time
import requests
import pandas as pd
from datetime import datetime

KS11_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/index/KS11/{}.csv"
MARKET_FLOW_PATH = "market_flow.json"

CACHE = {
    "value": None,
    "timestamp": 0
}

TTL = 300  # 5 min


def fetch_ks11(date: str):
    now = time.time()

    if CACHE["value"] and now - CACHE["timestamp"] < TTL:
        return CACHE["value"]

    try:
        r = requests.get(KS11_URL.format(date), timeout=10)

        if r.status_code != 200:
            return CACHE["value"]

        df = pd.read_csv(io.StringIO(r.text))

        for col in ["Close", "close"]:
            if col in df.columns:
                val = pd.to_numeric(df[col].iloc[-1], errors="coerce")
                if val > 0:
                    CACHE["value"] = float(val)
                    CACHE["timestamp"] = now
                    return CACHE["value"]

    except Exception as e:
        print(f"[KS11] error: {e}")

    return CACHE["value"]


def update_market_flow(date, kospi):
    try:
        try:
            with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = []

        data = [x for x in data if x.get("date") != date]

        data.append({
            "date": date,
            "kospi": kospi
        })

        data = sorted(data, key=lambda x: x["date"])[-300:]

        with open(MARKET_FLOW_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"[KS11] updated {kospi}")

    except Exception as e:
        print(f"[KS11] update error: {e}")


if __name__ == "__main__":
    date = datetime.now().strftime("%Y-%m-%d")

    kospi = fetch_ks11(date)

    if kospi:
        update_market_flow(date, kospi)
    else:
        print("[KS11] no data")
