import json
import pandas as pd
import numpy as np

from engine.normalizer import normalize_df
from engine.scorer import compute_score

from news_fetch import run as fetch_news


TOP_N = 10


# =========================
# LOAD DATA
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


# =========================
# FEATURES
# =========================

def build_flow_feature(df, flow_df):

    if flow_df.empty:
        df["flow"] = 0
        return df

    flow_df = flow_df.copy()

    short = flow_df.tail(5)
    mid = flow_df.tail(10)
    long = flow_df

    flow_score = (
        (short["foreign_net"].sum() + short["inst_net"].sum()) * 0.5 +
        (mid["foreign_net"].sum() + mid["inst_net"].sum()) * 0.3 +
        (long["foreign_net"].sum() + long["inst_net"].sum()) * 0.2
    )

    df["flow"] = flow_score
    return df


def build_news_feature(df):

    news_data = fetch_news()

    if not news_data:
        df["news"] = 0
        return df

    news_df = pd.DataFrame(news_data)

    # code 기준 merge
    df = df.merge(news_df, on="code", how="left")

    df["news"] = df["score"].fillna(0)

    return df


def build_momentum_feature(df):

    if "close" not in df.columns:
        df["momentum"] = 0
        return df

    df = df.copy()
    df["momentum"] = df.groupby("code")["close"].pct_change().fillna(0)

    return df


# =========================
# PIPELINE
# =========================

def run_pipeline():

    # 1. load
    df = load_data()

    # 2. normalize code
    df["code"] = (
        df["code"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )

    # 3. flow
    flow_df = load_flow()
    df = build_flow_feature(df, flow_df)

    # 4. news
    df = build_news_feature(df)

    # 5. momentum
    df = build_momentum_feature(df)

    # 6. normalize + scoring
    df = normalize_df(df)
    df = compute_score(df)

    # 7. ranking
    df = df.sort_values("score", ascending=False)

    top10 = df.head(TOP_N)
    top3 = top10.head(3)

    # 8. output
    result = {
        "top10": top10.to_dict("records"),
        "top3": top3.to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
