"""
fetch_data.py — v9.0 (CLEAN)
- KRX 전종목만 수집
- KS11 완전 제거 (외부 worker 분리)
"""

import io
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

DATA_PATH = "data.json"
KST = timezone(timedelta(hours=9))

FDR_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/{}.csv"
MAX_RETRIES = 7


def get_trading_dates():
    dates = []
    cur = datetime.now(timezone.utc)
    while len(dates) < MAX_RETRIES:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates


def fetch_fdr():
    for date in get_trading_dates():
        url = FDR_URL.format(date)

        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue

            df = pd.read_csv(io.StringIO(r.text))

            if len(df) < 50:
                continue

            col_map = {
                "Code": "code",
                "Symbol": "code",
                "Name": "name",
                "Close": "close",
                "Volume": "volume",
                "ChangeRatio": "change_rate",
                "ChgRatio": "change_rate",
            }

            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

            if not all(c in df.columns for c in ["code", "close", "volume"]):
                continue

            df["code"] = df["code"].astype(str).str.zfill(6)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

            df = df.dropna(subset=["close", "volume"])
            df = df[(df["close"] > 0) & (df["volume"] > 0)]

            if "change_rate" not in df.columns:
                df["change_rate"] = 0.0

            df = df[["code", "name", "close", "volume", "change_rate"]]

            print(f"[FDR] {date} OK → {len(df)} rows")
            return df, date

        except Exception as e:
            print(f"[FDR] error: {e}")

    raise RuntimeError("FDR fetch failed")


def save(df, date):
    payload = {
        "date": date,
        "count": len(df),
        "all": df.to_dict("records")
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[SAVE] data.json updated")


if __name__ == "__main__":
    print("[FETCH START]")

    df, date = fetch_fdr()
    save(df, date)

    print("[FETCH DONE]")
