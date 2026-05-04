"""
fetch_data.py — v8.2 FINAL STABLE
- UTC 기준 날짜 안정화
- FDR 실패 방어 강화
- 컬럼 변화 대응
"""

import io
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

DATA_PATH = "data.json"

FDR_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/{}.csv"

MAX_RETRIES = 7  # 7일 소급


def get_trading_dates():
    """UTC 기준 최근 평일 7일 생성"""
    dates = []
    cur = datetime.now(timezone.utc)

    while len(dates) < MAX_RETRIES:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)

    return dates


def fetch_fdr():
    last_error = None

    for date in get_trading_dates():
        url = FDR_URL.format(date)

        try:
            r = requests.get(url, timeout=10)

            if r.status_code != 200:
                print(f"[FDR] {date} HTTP {r.status_code} skip")
                continue

            df = pd.read_csv(io.StringIO(r.text))

            if len(df) < 50:
                print(f"[FDR] {date} too small ({len(df)}) skip")
                continue

            # =========================
            # SAFE COLUMN MAPPING
            # =========================
            col_map = {
                "Code": "code",
                "Name": "name",
                "Close": "close",
                "Volume": "volume",
                "Symbol": "code",
                "Adj Close": "close",
            }

            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

            required = ["code", "close", "volume"]

            if not all(c in df.columns for c in required):
                print(f"[FDR] {date} missing columns → skip")
                continue

            df["code"] = df["code"].astype(str).str.zfill(6)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

            df = df.dropna(subset=["close", "volume"])
            df = df[(df["close"] > 0) & (df["volume"] > 0)]

            if len(df) < 50:
                print(f"[FDR] {date} after clean too small → skip")
                continue

            df = df[["code", "name", "close", "volume"]].copy()

            print(f"[FDR] {date} OK → {len(df)} rows")
            return df, date

        except Exception as e:
            last_error = e
            print(f"[FDR] {date} error → skip: {e}")

    raise RuntimeError(f"FDR fetch failed after {MAX_RETRIES} days → {last_error}")


def save(df, date):
    payload = {
        "date": date,
        "count": len(df),
        "all": df.to_dict("records"),
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[SAVE] data.json updated")


if __name__ == "__main__":
    print("[FETCH START]")
    df, date = fetch_fdr()
    save(df, date)
    print("[FETCH DONE]")
