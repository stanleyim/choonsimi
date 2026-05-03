"""
fetch_data.py — v7 FINAL (STABLE)
"""

import os
import json
import requests
import pandas as pd
import io
from datetime import datetime

DATA_PATH = "data.json"

FDR_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/{}.csv"


def get_latest_date():
    return datetime.today().strftime("%Y-%m-%d")


def fetch_fdr():
    date = get_latest_date()

    url = FDR_URL.format(date)

    r = requests.get(url, timeout=10)

    if r.status_code != 200:
        raise Exception("FDR fetch 실패")

    df = pd.read_csv(io.StringIO(r.text))

    df["code"] = df["Code"].astype(str).str.zfill(6)
    df["name"] = df.get("Name", "")

    df["close"] = pd.to_numeric(df["Close"], errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)

    df = df[df["close"] > 0]
    df = df[df["volume"] > 0]

    df["foreign_net"] = 0
    df["inst_net"] = 0
    df["dart_score"] = 0

    print(f"[FDR] {len(df)} 종목")

    return df, date


def save(df, date):
    payload = {
        "date": date,
        "count": len(df),
        "all": df.to_dict("records"),
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[SAVE] 완료")


if __name__ == "__main__":
    print("[FETCH START]")

    df, date = fetch_fdr()

    save(df, date)

    print("[FETCH DONE]")
