"""
engine/engine.py — v37 FINAL STABLE (PRODUCTION READY FIXED)
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
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH = os.path.join(ROOT, "result.json")

TOP_N = 10
EPS = 1e-9


# =========================
# LOAD STOCK
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
# HISTORY
# =========================
def load_history():
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame(columns=["code", "score", "date"])

    return pd.read_csv(HISTORY_PATH)


# =========================
# REGIME
# =========================
def get_regime(flow):
    if flow is None or len(flow) < 5:
        return "NEUTRAL"

    trend = flow["net"].rolling(5).mean().iloc[-1]

    if trend > 0:
        return "RISK_ON"
    elif trend < 0:
        return "RISK_OFF"
    return "NEUTRAL"


# =========================
# FEATURES
# =========================
def build_features(df, news_map):

    df["momentum"] = df["close"].pct_change().rolling(3).mean().fillna(0)

    df["breakout"] = (
        df["close"] > df["close"].rolling(5).max().shift(1)
    ).astype(int)

    df["vol_shock"] = df["volume"] / (df["volume"].rolling(5).mean() + EPS)

    df["news"] = df["code"].map(news_map).fillna(0)

    return df


# =========================
# NORMALIZE
# =========================
def normalize(df, cols):
    for c in cols:
        std = df[c].std()
        df[c] = (df[c] - df[c].mean()) / (std + EPS)
    return df


# =========================
# SCORE ENGINE
# =========================
def compute_score(df, regime):

    if regime == "RISK_ON":
        w = (0.4, 0.3, 0.2, 0.1)
    elif regime == "RISK_OFF":
        w = (0.25, 0.35, 0.2, 0.2)
    else:
        w = (0.3, 0.3, 0.2, 0.2)

    return (
        df["news"] * w[0] +
        df["momentum"] * w[1] +
        df["breakout"] * w[2] +
        df["vol_shock"] * w[3]
    )


# =========================
# STABILITY LAYER
# =========================
def stability(df, history):

    if history is None or len(history) == 0:
        df["final"] = df["score"]
        return df

    prev = history.set_index("code")["score"].to_dict()

    df["prev_score"] = df["code"].map(prev).fillna(0)

    df["final"] = 0.7 * df["score"] + 0.3 * df["prev_score"]

    return df


# =========================
# FILTER
# =========================
def liquidity(df):
    df["value"] = df["close"] * df["volume"]
    return df[df["value"] > df["value"].quantile(0.3)]


# =========================
# MAIN
# =========================
def run():

    print("[ENGINE v37 FINAL START]")

    df, date = load_stock()
    news_map = load_news()
    flow = load_flow()
    history = load_history()

    regime = get_regime(flow)

    print("[REGIME]", regime)

    df = liquidity(df)

    if df is None or len(df) == 0:
        print("[NO DATA]")
        return

    df = build_features(df, news_map)

    df = normalize(df, ["momentum", "vol_shock", "news"])

    df["score"] = compute_score(df, regime)

    df = stability(df, history)

    df = df.dropna(subset=["final"])

    top = df.sort_values("final", ascending=False).head(TOP_N)

    # =========================
    # SAFE OUTPUT
    # =========================
    top = top.loc[:, ~top.columns.duplicated()].copy()
    top = top.rename(columns={"final": "score"})

    result = {
        "date": date,
        "regime": regime,
        "top10": top[["code", "close", "score"]].round(4).to_dict("records")
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # =========================
    # HISTORY SAFE APPEND
    # =========================
    save = top[["code", "score"]].copy()
    save["date"] = date

    if os.path.exists(HISTORY_PATH):
        old = pd.read_csv(HISTORY_PATH)
        save = pd.concat([old, save], ignore_index=True)

    save.to_csv(HISTORY_PATH, index=False)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    run()
