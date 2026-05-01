import json
import pandas as pd
import requests
from datetime import datetime, timedelta

def get_date():
    return datetime.now().strftime("%Y-%m-%d")


def fetch():
    url = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/latest.csv"
    df = pd.read_csv(url)
    return df


def build(df):
    out = pd.DataFrame()
    out["code"] = df["Code"].astype(str).str.zfill(6)
    out["close"] = pd.to_numeric(df["Close"], errors="coerce").fillna(0)
    out["volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    out["dart_score"] = 0.0

    out = out[out["close"] > 0]
    return out.to_dict("records")


def save(records):
    data = {
        "date": get_date(),
        "all": records
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    df = fetch()
    records = build(df)
    save(records)
