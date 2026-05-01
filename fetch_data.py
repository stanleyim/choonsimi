"""
fetch_data.py — Universe Builder (FINAL STABLE)
"""

import io
import json
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "data.json")

MIN_STOCKS = 50
MAX_DAYS = 10
TIMEOUT = 20

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "FinanceData/fdr_krx_data_cache/"
    "refs/heads/master/data/listing/krx/{date}.csv"
)

COL = {
    "code": ["Code", "code", "Symbol"],
    "name": ["Name", "name"],
    "close": ["Close", "close"],
    "volume": ["Volume", "volume"]
}


def get_date_list():
    dates = []
    d = datetime.today()
    while len(dates) < MAX_DAYS:
        if d.weekday() < 5:
            dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return dates


def fetch_csv(date):
    url = BASE_URL.format(date=date)
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        df = pd.read_csv(io.StringIO(r.text))
        if len(df) < MIN_STOCKS:
            return None
        return df
    except:
        return None


def pick(df, keys):
    for k in keys:
        if k in df.columns:
            return k
    return None


def build(df, date):
    c_code = pick(df, COL["code"])
    c_name = pick(df, COL["name"])
    c_close = pick(df, COL["close"])
    c_vol = pick(df, COL["volume"])

    if not all([c_code, c_close, c_vol]):
        raise Exception("missing columns")

    out = pd.DataFrame()
    out["code"] = df[c_code].astype(str).str.zfill(6)
    out["name"] = df[c_name] if c_name else ""
    out["close"] = pd.to_numeric(df[c_close], errors="coerce").fillna(0)
    out["volume"] = pd.to_numeric(df[c_vol], errors="coerce").fillna(0).astype(int)

    out = out[out["close"] > 0]
    out = out[out["volume"] > 0]

    out["foreign_net"] = 0.0
    out["inst_net"] = 0.0
    out["dart_score"] = 0.0

    out["date"] = date
    return out.to_dict("records")


def save(records, date):
    payload = {
        "date": date,
        "count": len(records),
        "all": records
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print("[BUILD UNIVERSE]")

    df = None
    used = None

    for d in get_date_list():
        df = fetch_csv(d)
        if df is not None:
            used = d
            break

    if df is None:
        raise Exception("no data")

    records = build(df, used)

    if len(records) < MIN_STOCKS:
        raise Exception("insufficient universe")

    save(records, used)

    print("[DONE]", len(records))
