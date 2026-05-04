"""
fetch_data.py — v8 FINAL CLEAN (ENGINE SAFE SCHEMA)
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

    # =========================
    # CLEAN CORE SCHEMA ONLY
    # =========================
    df = df.rename(columns={
        "Code": "code",
        "Name": "name",
        "Close": "close",
        "Volume": "volume"
    })

    df["code"] = df["code"].astype(str).str.zfill(6)

    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    # remove invalid rows
    df = df.dropna(subset=["close", "volume"])
    df = df[(df["close"] > 0) & (df["volume"] > 0)]

    # =========================
    # ENGINE-REQUIRED FIELDS ONLY
    # =========================
    df = df[["code", "name", "close", "volume"]].copy()

    print(f"[FDR] {len(df)} 종목")

    return df, date


def save(df, date):

    payload = {
        "date": date,
        "count": len(df),
        "all": df.to_dict("records")
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[SAVE] 완료")


if __name__ == "__main__":

    print("[FETCH START]")

    df, date = fetch_fdr()

    save(df, date)

    print("[FETCH DONE]")
