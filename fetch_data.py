"""
fetch_data.py — v8.1 FINAL CLEAN (날짜 소급 추가)
"""

import io
import json
import os
import requests
import pandas as pd
from datetime import datetime, timedelta

DATA_PATH = "data.json"

FDR_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/{}.csv"

MAX_RETRIES = 7  # 최대 7일 소급 (주말 + 공휴일 대응)


def get_trading_dates():
    """오늘부터 소급 평일 날짜 리스트."""
    dates = []
    cur   = datetime.today()
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
                print(f"[FDR] {date} HTTP {r.status_code} → skip")
                continue

            df = pd.read_csv(io.StringIO(r.text))

            if len(df) < 50:
                print(f"[FDR] {date} 종목 부족({len(df)}) → skip")
                continue

            # =========================
            # CLEAN CORE SCHEMA ONLY
            # =========================
            df = df.rename(columns={
                "Code":   "code",
                "Name":   "name",
                "Close":  "close",
                "Volume": "volume",
            })

            df["code"]   = df["code"].astype(str).str.zfill(6)
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

            df = df.dropna(subset=["close", "volume"])
            df = df[(df["close"] > 0) & (df["volume"] > 0)]

            # =========================
            # ENGINE-REQUIRED FIELDS ONLY
            # =========================
            df = df[["code", "name", "close", "volume"]].copy()

            print(f"[FDR] {date} → {len(df)}종목")
            return df, date

        except Exception as e:
            print(f"[FDR] {date} 오류: {e} → skip")

    raise RuntimeError(f"FDR 데이터 수집 실패 (최근 {MAX_RETRIES}일)")


def save(df, date):
    payload = {
        "date":  date,
        "count": len(df),
        "all":   df.to_dict("records"),
    }
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[SAVE] 완료")


if __name__ == "__main__":
    print("[FETCH START]")
    df, date = fetch_fdr()
    save(df, date)
    print("[FETCH DONE]")
