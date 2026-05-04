"""
engine/engine.py — v34 FINAL STABLE (IMPROVED)
NON-PREDICTIVE KRX RANKING ENGINE
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_PATH = os.path.join(ROOT, "data.json")
NEWS_PATH = os.path.join(ROOT, "news.json")
FLOW_PATH = os.path.join(ROOT, "market_flow.json")
RESULT_PATH = os.path.join(ROOT, "result.json")

TOP_N = 10
EPS = 1e-9


# =========================
def load_stock():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df["code"] = df["code"].astype(str).str.zfill(6)

    df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    return df, raw.get("date")


# =========================
def load_news():
    if not os.path.exists(NEWS_PATH):
        return {}
    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {x["code"]: x["score"] for x in data}


# =========================
def load_flow():
    if not os.path.exists(FLOW_PATH):
        return pd.DataFrame()

    df = pd.read_json(FLOW_PATH)
    df["net"] = df["foreign_net"] + df["inst_net"]
    return df


# =========================
def regime(flow_df):
    if len(flow_df) < 10:
        return "NEUTRAL"

    net = flow_df["net"].rolling(5).mean().iloc[-1]

    if net > 0:
        return "RISK_ON"
    if net < 0:
        return "RISK_OFF"
    return "NEUTRAL"


# =========================
def features(df, news_map):

    df["momentum"] = df["close"].pct_change().rolling(3).mean().fillna(0)

    df["breakout"] = (
        df["close"] > df["close"].rolling(5).max().shift(1)
    ).astype(int)

    df["vol_shock"] = df["volume"] / (df["volume"].rolling(5).mean() + EPS)

    df["news"] = df["code"].map(news_map).fillna(0) * 0.35

    return df


# =========================
def liquidity_filter(df):
    df["value"] = df["close"] * df["volume"]

    threshold = df["value"].quantile(0.3)  # 🔥 개선 핵심
    return df[df["value"] > threshold]


# =========================
def score(df, regime):

    w_news, w_mom, w_break, w_vol = 0.35, 0.30, 0.20, 0.15

    s = (
        df["news"] * w_news +
        df["momentum"] * w_mom +
        df["breakout"] * w_break +
        df["vol_shock"] * w_vol
    )

    if regime == "RISK_ON":
        s *= 1.05
    elif regime == "RISK_OFF":
        s *= 0.95

    # outlier clipping (중요)
    s = np.clip(s, s.quantile(0.01), s.quantile(0.99))

    return s


# =========================
def run():

    print("[ENGINE v34 START]")

    df, date = load_stock()
    news_map = load_news()
    flow_df = load_flow()

    r = regime(flow_df)
    print("[REGIME]", r)

    df = liquidity_filter(df)

    if len(df) == 0:
        print("[NO DATA]")
        return

    df = features(df, news_map)

    df["score"] = score(df, r)

    top = df.sort_values("score", ascending=False).head(TOP_N)

    result = {
        "date": date,
        "regime": r,
        "top10": top[["code", "close", "score"]].to_dict("records")
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    run()
