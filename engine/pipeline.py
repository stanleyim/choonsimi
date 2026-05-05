import json
import os
import pandas as pd
import numpy as np
from datetime import datetime

from engine.normalizer import normalize_df
from engine.scorer import compute_score
from engine.history import append_history, HISTORY_PATH

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

def build_market_sentiment(flow_df):
    """
    market_flow.json = 시장 전체 외국인/기관 순매수
    → per-stock 스코어링 불가
    → market_context 로 별도 출력
    """

    if flow_df.empty:
        return {"label": "데이터없음", "score": 0.0, "foreign_5d": 0, "inst_5d": 0}

    flow_df = flow_df.copy().sort_values("date").reset_index(drop=True)

    short = flow_df.tail(5)
    mid   = flow_df.tail(10)
    long  = flow_df

    foreign_score = (
        short["foreign_net"].sum() * 0.5 +
        mid["foreign_net"].sum()   * 0.3 +
        long["foreign_net"].sum()  * 0.2
    )
    inst_score = (
        short["inst_net"].sum() * 0.5 +
        mid["inst_net"].sum()   * 0.3 +
        long["inst_net"].sum()  * 0.2
    )

    combined = foreign_score * 0.7 + inst_score * 0.3

    max_val = flow_df[["foreign_net", "inst_net"]].abs().max().max()
    normalized = float(np.clip(combined / (max_val * 10), -1.0, 1.0)) if max_val > 0 else 0.0

    if normalized > 0.1:
        label = "강세"
    elif normalized < -0.1:
        label = "약세"
    else:
        label = "중립"

    return {
        "label": label,
        "score": round(normalized, 4),
        "foreign_5d": int(short["foreign_net"].sum()),
        "inst_5d":    int(short["inst_net"].sum())
    }


def build_news_feature(df):
    """
    ✅ news_df score 컬럼 충돌 방지 → news_score로 rename
    """

    news_data = fetch_news()

    if not news_data:
        df["news"] = 0
        return df

    news_df = pd.DataFrame(news_data)

    if "score" in news_df.columns:
        news_df = news_df.rename(columns={"score": "news_score"})

    merge_cols = ["code", "news_score"] if "news_score" in news_df.columns else ["code"]
    df = df.merge(news_df[merge_cols], on="code", how="left")

    if "news_score" in df.columns:
        df["news"] = df["news_score"].fillna(0)
        df = df.drop(columns=["news_score"])
    else:
        df["news"] = 0

    return df


def build_momentum_feature(df):
    """
    우선순위:
    1) data.json change_rate 컬럼 직접 사용
    2) history.csv 전날 close 기반 계산
    3) fallback → 0
    """

    df = df.copy()

    # 1) change_rate 컬럼 직접 사용
    for col in ["change_rate", "chg_rate", "pct_change", "rate"]:
        if col in df.columns:
            df["momentum"] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            print(f"[MOMENTUM] {col} 직접 사용")
            return df

    # 2) history.csv 전날 close 기반
    if os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0 and "close" in df.columns:
        try:
            hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})

            if "close" in hist.columns and "date" in hist.columns:
                latest_date = hist["date"].max()
                prev = hist[hist["date"] == latest_date][["code", "close"]].copy()
                prev["code"] = (
                    prev["code"]
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                    .str.replace(r"[^0-9]", "", regex=True)
                    .str.zfill(6)
                )
                prev = prev.rename(columns={"close": "close_prev"})
                df = df.merge(prev, on="code", how="left")
                df["momentum"] = (
                    (df["close"] - df["close_prev"]) / df["close_prev"]
                ).fillna(0)
                df = df.drop(columns=["close_prev"])
                print("[MOMENTUM] history.csv 전날 close 기반 계산")
                return df
        except Exception as e:
            print(f"[MOMENTUM] history 로드 실패: {e}")

    # 3) fallback
    df["momentum"] = 0
    print("[MOMENTUM] fallback → momentum=0")
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

    # 3. market sentiment
    flow_df = load_flow()
    market_context = build_market_sentiment(flow_df)
    print(f"[MARKET] {market_context['label']} (score: {market_context['score']})")

    # 4. news
    df = build_news_feature(df)

    # 5. momentum
    df = build_momentum_feature(df)

    # 6. scoring (자동 가중치)
    df = compute_score(df)

    # 7. normalize (최종 score 기준 정렬 + 중복제거)
    df = normalize_df(df)

    # 8. ranking
    df = df.sort_values("score", ascending=False)

    top10 = df.head(TOP_N)
    top3  = top10.head(3)

    # 9. output
    result = {
        "market_context": market_context,
        "top10": top10.to_dict("records"),
        "top3":  top3.to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] top10 저장 완료 | 시장: {market_context['label']}")

    # 10. history 누적 저장
    append_history(top10)
