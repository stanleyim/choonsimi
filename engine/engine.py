"""
engine/engine.py — v37 FINAL (PRODUCTION TOP10 SYSTEM)
GOAL: STABLE + CONSISTENT + MARKET ADAPTIVE TOP 10
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
# LOAD
# =========================
def load_stock():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df["code"] = df["code"].astype(str).str.zfill(6)

    df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    return df, raw.get("date")


def load_news():
    if not os.path.exists(NEWS_PATH):
        return {}
    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {x["code"]: x["score"] for x in data}


def load_flow():
    if not os.path.exists(FLOW_PATH):
        return pd.DataFrame()
    df = pd.read_json(FLOW_PATH)
    df["net"] = df["foreign_net"] + df["inst_net"]
    return df


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame()
    return pd.read_csv(HISTORY_PATH)


# =========================
# MARKET REGIME
# =========================
def get_regime(flow_df):

    if len(flow_df) < 5:
        return "NEUTRAL"

    trend = flow_df["net"].rolling(5).mean().iloc[-1]

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
# NORMALIZATION
# =========================
def normalize(df, cols):
    for c in cols:
        df[c] = (df[c] - df[c].mean()) / (df[c].std() + EPS)
    return df


# =========================
# SCORING
# =========================
def score(df, regime):

    if regime == "RISK_ON":
        w = {"news":0.4, "momentum":0.3, "breakout":0.2, "vol":0.1}
    elif regime == "RISK_OFF":
        w = {"news":0.25, "momentum":0.35, "breakout":0.2, "vol":0.2}
    else:
        w = {"news":0.3, "momentum":0.3, "breakout":0.2, "vol":0.2}

    return (
        df["news"] * w["news"] +
        df["momentum"] * w["momentum"] +
        df["breakout"] * w["breakout"] +
        df["vol_shock"] * w["vol"]
    )


# =========================
# STABILITY LAYER (핵심)
# =========================
def stability_merge(df, history):

    if len(history) == 0:
        df["final"] = df["score"]
        return df

    prev = history[["code", "score"]].set_index("code").to_dict()["score"]

    df["prev_score"] = df["code"].map(prev).fillna(0)

    df["final"] = 0.7 * df["score"] + 0.3 * df["prev_score"]

    return df


# =========================
# LIQUIDITY FILTER
# =========================
def liquidity(df):
    df["value"] = df["close"] * df["volume"]
    return df[df["value"] > df["value"].quantile(0.3)]


# =========================
# MAIN
# =========================
def run():

    print("[ENGINE v37 START]")

    df, date = load_stock()
    news_map = load_news()
    flow = load_flow()
    history = load_history()

    regime = get_regime(flow)

    print("[REGIME]", regime)

    df = liquidity(df)

    if len(df) == 0:
        print("[NO DATA]")
        return

    df = build_features(df, news_map)

    df = normalize(df, ["momentum", "vol_shock", "news"])

    df["score"] = score(df, regime)

    df = stability_merge(df, history)

    top = df.sort_values("final", ascending=False).head(TOP_N)

    result = {
        "date": date,
        "regime": regime,
        "top10": top[["code", "close", "final"]].to_dict("records")
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # history update
    save = top[["code", "final"]].rename(columns={"final":"score"})
    save["date"] = date

    if os.path.exists(HISTORY_PATH):
        old = pd.read_csv(HISTORY_PATH)
        save = pd.concat([old, save])

    save.to_csv(HISTORY_PATH, index=False)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    run()
