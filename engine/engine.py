"""
engine.py — Choonsimi FINAL STABLE ENGINE (Normalized)
- flow / momentum / news / volume scale 통일
- cross-sectional ranking 안정화
"""

import json
import pandas as pd
import numpy as np

TOP_N = 10


# =========================
# LOAD
# =========================

def load_data():
    with open("data.json", "r", encoding="utf-8") as f:
        return pd.DataFrame(json.load(f)["all"])


def load_flow():
    try:
        with open("market_flow.json", "r", encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))
    except:
        return pd.DataFrame()


def load_news():
    try:
        with open("news.json", "r", encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))
    except:
        return pd.DataFrame()


# =========================
# NORMALIZATION
# =========================

def normalize_series(s):
    s = s.replace([np.inf, -np.inf], 0).fillna(0)
    return (s - s.mean()) / (s.std() + 1e-9)


# =========================
# FEATURES
# =========================

def build_momentum(df):
    df = df.copy()

    if "close" in df.columns:
        df["momentum"] = df.groupby("code")["close"].pct_change().fillna(0)

    return df


def build_flow(df, flow_df):
    if flow_df.empty:
        df["flow"] = 0
        return df

    flow_df = flow_df.copy()

    # multi horizon flow
    short = flow_df.tail(5)
    mid = flow_df.tail(10)
    long = flow_df

    short_flow = short["foreign_net"].sum() + short["inst_net"].sum()
    mid_flow = mid["foreign_net"].sum() + mid["inst_net"].sum()
    long_flow = long["foreign_net"].sum() + long["inst_net"].sum()

    flow_score = (
        short_flow * 0.5 +
        mid_flow * 0.3 +
        long_flow * 0.2
    )

    df["flow"] = flow_score

    return df


def build_news(df, news_df):
    if news_df.empty:
        df["news"] = 0
        return df

    news_df = news_df.copy()
    news_df["code"] = news_df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)

    agg = news_df.groupby("code").size().reset_index(name="news")

    df = df.merge(agg, on="code", how="left").fillna(0)

    df["news"] = np.log1p(df["news"])

    return df


# =========================
# SCORING
# =========================

def compute_score(df):
    df = df.copy()

    for c in ["news", "momentum", "volume", "flow"]:
        if c not in df.columns:
            df[c] = 0

    df = df.replace([np.inf, -np.inf], 0).fillna(0)

    # volume 안정화
    df["volume"] = np.log1p(df["volume"])

    # ===== NORMALIZATION =====
    df["flow"] = normalize_series(df["flow"])
    df["momentum"] = normalize_series(df["momentum"])
    df["news"] = normalize_series(df["news"])
    df["volume"] = normalize_series(df["volume"])

    # ===== FINAL SCORE MODEL =====
    df["score"] = (
        df["flow"] * 0.40 +
        df["momentum"] * 0.30 +
        df["volume"] * 0.20 +
        df["news"] * 0.10
    )

    return df


# =========================
# PIPELINE
# =========================

def run_pipeline():

    df = load_data()

    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)

    df = build_momentum(df)

    flow_df = load_flow()
    df = build_flow(df, flow_df)

    news_df = load_news()
    df = build_news(df, news_df)

    df = compute_score(df)

    df = df.sort_values("score", ascending=False)

    top10 = df.head(TOP_N)
    top3 = top10.head(3)

    result = {
        "top10": top10.to_dict("records"),
        "top3": top3.to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_pipeline()
