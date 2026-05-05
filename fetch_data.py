"""
fetch_data.py — v8.4
- ChagesRatio (FDR 오타) 컬럼 대응 추가
- change_rate 컬럼 안정적 확보
"""

import io
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

DATA_PATH = "data.json"

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
                "Code":        "code",
                "Symbol":      "code",
                "Name":        "name",
                "Close":       "close",
                "Adj Close":   "close",
                "Volume":      "volume",
                # ✅ change_rate — FDR 버전별 + 오타 전체 대응
                "ChagesRatio": "change_rate",  # FDR 실제 오타 컬럼명
                "ChgRatio":    "change_rate",
                "ChangeRatio": "change_rate",
                "Chg":         "change_rate",
                "Change":      "change_rate",
                "Returns":     "change_rate",
            }

            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

            # 컬럼 확인 로그
            print(f"[FDR] {date} columns: {list(df.columns)}")

            required = ["code", "close", "volume"]
            if not all(c in df.columns for c in required):
                print(f"[FDR] {date} missing columns → skip")
                continue

            df["code"]   = df["code"].astype(str).str.zfill(6)
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

            df = df.dropna(subset=["close", "volume"])
            df = df[(df["close"] > 0) & (df["volume"] > 0)]

            if len(df) < 50:
                print(f"[FDR] {date} after clean too small → skip")
                continue

            # ✅ change_rate 확보
            if "change_rate" in df.columns:
                df["change_rate"] = pd.to_numeric(df["change_rate"], errors="coerce").fillna(0)
                print(f"[FDR] change_rate 컬럼 확보 ✅")
            else:
                df["change_rate"] = 0.0
                print(f"[FDR] change_rate 컬럼 없음 → 0 처리")

            df = df[["code", "name", "close", "volume", "change_rate"]].copy()

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
