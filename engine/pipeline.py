import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

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
    ✅ Fix #3: market_flow.json은 시장 전체 데이터 (code 없음)
    → per-stock 스코어링 사용 불가
    → 시장 심리 지표로 별도 계산, result.json에 market_context로 출력
    
    반환값: dict
      - label: "강세" / "중립" / "약세"
      - score: float (-1.0 ~ 1.0)
      - foreign_5d: 최근 5일 외국인 순매수 합계
      - inst_5d: 최근 5일 기관 순매수 합계
    """

    if flow_df.empty:
        return {"label": "데이터없음", "score": 0.0, "foreign_5d": 0, "inst_5d": 0}

    flow_df = flow_df.copy()
    flow_df = flow_df.sort_values("date").reset_index(drop=True)

    short = flow_df.tail(5)
    mid   = flow_df.tail(10)
    long  = flow_df

    # 가중 합산 (단위 통일 필요 — foreign_net이 inst_net보다 훨씬 큼)
    foreign_score = (
        short["foreign_net"].sum() * 0.5 +
        mid["foreign_net"].sum() * 0.3 +
        long["foreign_net"].sum() * 0.2
    )
    inst_score = (
        short["inst_net"].sum() * 0.5 +
        mid["inst_net"].sum() * 0.3 +
        long["inst_net"].sum() * 0.2
    )

    # 외국인 70% + 기관 30% 합산 (외국인이 시장 주도)
    combined = foreign_score * 0.7 + inst_score * 0.3

    # -1 ~ 1 정규화
    max_val = flow_df[["foreign_net", "inst_net"]].abs().max().max()
    if max_val == 0:
        normalized = 0.0
    else:
        normalized = float(np.clip(combined / (max_val * 10), -1.0, 1.0))

    # 라벨
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
        "inst_5d": int(short["inst_net"].sum())
    }


def build_news_feature(df):
    """
    ✅ Fix #1: news_df score 컬럼 충돌 방지 → news_score로 rename 후 merge
    """

    news_data = fetch_news()

    if not news_data:
        df["news"] = 0
        return df

    news_df = pd.DataFrame(news_data)

    # score 컬럼명 충돌 방지 (핵심 수정)
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
    ✅ Fix #2: 하루치 스냅샷 pct_change() 항상 0 문제 해결
    우선순위:
    1) data.json에 change_rate 컬럼 있으면 직접 사용
    2) history.csv 전날 close 기반 계산
    3) fallback → 0
    """

    df = df.copy()

    # 1) change_rate 컬럼 직접 사용
    for col in ["change_rate", "chg_rate", "pct_change", "rate"]:
        if col in df.columns:
            df["momentum"] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            print(f"[MOMENTUM] {col} 컬럼 직접 사용")
            return df

    # 2) history.csv 전날 close 기반
    if os.path.exists(HISTORY_PATH) and "close" in df.columns:
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

    # 3. flow → market sentiment (per-stock 아님)
    flow_df = load_flow()
    market_context = build_market_sentiment(flow_df)
    print(f"[MARKET] {market_context['label']} (score: {market_context['score']})")

    # 4. news
    df = build_news_feature(df)

    # 5. momentum
    df = build_momentum_feature(df)

    # ✅ Fix #4: compute_score 먼저, normalize_df 나중에
    # 6. scoring (최종 score 계산)
    df = compute_score(df)

    # 7. normalize (최종 score 기준 정렬 + 중복 제거)
    df = normalize_df(df)

    # 8. ranking
    df = df.sort_values("score", ascending=False)

    top10 = df.head(TOP_N)
    top3 = top10.head(3)

    # 9. output — result.json (market_context 포함)
    result = {
        "market_context": market_context,
        "top10": top10.to_dict("records"),
        "top3": top3.to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] top10 저장 완료 | 시장: {market_context['label']}")

    # 10. history 누적 저장
    append_history(top10)
