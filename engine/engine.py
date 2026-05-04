"""
engine/engine.py — v33 FINAL STABLE (PRODUCTION READY)
NON-PREDICTIVE KRX RANKING ENGINE
STRUCTURE LOCKED
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

# =========================
# PATH
# =========================
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_PATH = os.path.join(ROOT, "data.json")
NEWS_PATH = os.path.join(ROOT, "news.json")
FLOW_PATH = os.path.join(ROOT, "market_flow.json")
RESULT_PATH = os.path.join(ROOT, "result.json")

TOP_N = 10
EPS = 1e-9


# =========================
# LOAD STOCK DATA
# =========================
def load_stock():

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df["code"] = df["code"].astype(str).str.zfill(6)

    df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))


# =========================
# LOAD NEWS
# =========================
def load_news():

    if not os.path.exists(NEWS_PATH):
        return {}

    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {x["code"]: x["score"] for x in data}


# =========================
# LOAD FLOW
# =========================
def load_flow():

    if not os.path.exists(FLOW_PATH):
        return pd.DataFrame()

    df = pd.read_json(FLOW_PATH)

    df["net"] = df["foreign_net"] + df["inst_net"]

    return df


# =========================
# REGIME (MARKET STATE)
# =========================
def get_regime(flow_df):

    if flow_df is None or len(flow_df) < 5:
        return "NEUTRAL"

    recent = flow_df.tail(20)

    net = recent["net"].sum()

    if net > 0:
        return "RISK_ON"
    elif net < 0:
        return "RISK_OFF"
    return "NEUTRAL"


# =========================
# FEATURE ENGINEERING (LAG FREE)
# =========================
def build_features(df, news_map):

    # acceleration momentum (lag reduced)
    df["momentum"] = df["close"].pct_change().diff().fillna(0)

    # breakout (no lookahead bias)
    df["breakout"] = (
        df["close"].shift(1) >
        df["close"].rolling(5).max().shift(1)
    ).astype(int)

    # volume shock (early signal)
    df["vol_shock"] = df["volume"] / (df["volume"].rolling(5).mean() + EPS)

    # news signal (stable scaled)
    df["news"] = df["code"].map(news_map).fillna(0)

    # decay to reduce lag influence
    df["news"] = df["news"] * 0.35

    return df


# =========================
# LIQUIDITY FILTER
# =========================
def liquidity_filter(df):

    df["value"] = df["close"] * df["volume"]

    return df[df["value"] > 3e9]


# =========================
# SCORING ENGINE
# =========================
def compute_score(df, regime):

    # fixed stable weights (no IC jitter)
    w_news = 0.35
    w_mom = 0.30
    w_break = 0.20
    w_vol = 0.15

    score = (
        df["news"] * w_news +
        df["momentum"] * w_mom +
        df["breakout"] * w_break +
        df["vol_shock"] * w_vol
    )

    # regime adjustment (soft multiplier)
    if regime == "RISK_ON":
        score *= 1.08
    elif regime == "RISK_OFF":
        score *= 0.92

    return score


# =========================
# MAIN
# =========================
def run():

    print("[ENGINE v33 START]")

    df, date = load_stock()
    news_map = load_news()
    flow_df = load_flow()

    regime = get_regime(flow_df)
    print(f"[REGIME] {regime}")

    # filter
    df = liquidity_filter(df)

    if len(df) == 0:
        print("[NO DATA]")
        return

    # features
    df = build_features(df, news_map)

    # score
    df["score"] = compute_score(df, regime)

    # ranking
    top = df.sort_values("score", ascending=False).head(TOP_N)

    result = {
        "date": date,
        "regime": regime,
        "top10": top[["code", "close", "score"]].to_dict("records")
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    run()
