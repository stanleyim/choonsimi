"""
fetch_data.py — v7 FINAL (STABLE + FLOW_COVERAGE)
"""

import io, json, os, shutil, time
from datetime import datetime, timedelta
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "data.json")
BACKUP_FILE = os.path.join(ROOT, "data.json.bak")

FDR_CACHE_URL = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/refs/heads/master/data/listing/krx/{date}.csv"

NAVER_URL = "https://finance.naver.com/sise/sise_trade_investor.naver"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com"
}

def trading_dates(n=10):
    dates, cur = [], datetime.today()
    while len(dates) < n:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates

def fetch_price():
    for d in trading_dates():
        url = FDR_CACHE_URL.format(date=d)
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            df = pd.read_csv(io.StringIO(r.text))
            if len(df) < 100:
                continue

            df["code"] = df["Code"].astype(str).str.zfill(6)
            df["name"] = df.get("Name", "")
            df["close"] = pd.to_numeric(df["Close"], errors="coerce")
            df["volume"] = pd.to_numeric(df["Volume"], errors="coerce")

            df = df[(df["close"] > 0) & (df["volume"] > 0)]
            return df, d, "fdr_cache"
        except:
            continue

    return pd.DataFrame(), datetime.today().strftime("%Y-%m-%d"), "fallback"

def fetch_flow(date):
    try:
        r = requests.get(NAVER_URL, params={"bizdate": date.replace("-", "")}, headers=HEADERS)
        tables = pd.read_html(r.text)
        df = tables[0]

        df["code"] = df["종목코드"].astype(str).str.zfill(6)
        df["foreign_net"] = pd.to_numeric(df["외국인"], errors="coerce").fillna(0)
        df["inst_net"] = pd.to_numeric(df["기관"], errors="coerce").fillna(0)

        return df[["code", "foreign_net", "inst_net"]]
    except:
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

def main():

    print("[FETCH START]")

    df, date, source = fetch_price()

    flow = fetch_flow(date)
    df = df.merge(flow, on="code", how="left")

    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"] = df["inst_net"].fillna(0)

    flow_cov = ((df["foreign_net"] != 0) | (df["inst_net"] != 0)).mean()

    payload = {
        "date": date,
        "source": source,
        "flow_coverage": float(flow_cov),
        "count": len(df),
        "all": df.to_dict("records")
    }

    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)

    with open(DATA_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"[DONE] {len(df)} stocks / flow_cov={flow_cov:.2f}")

if __name__ == "__main__":
    main()
