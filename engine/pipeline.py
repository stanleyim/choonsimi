"""
engine/pipeline.py — v3.0 FINAL (PRODUCT READY)
목적: 아침 추천용 TOP10 생성
구조: DATA → FILTER → SIGNAL → ENTRY → RANK → OUTPUT
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from engine.normalizer import normalize_df
from engine.signal import compute_signal
from engine.regime import get_regime
from engine.history import append_history

from news_fetch import run as fetch_news

TOP_N = 10


# =========================
# LOAD
# =========================

def load_data():
    with open("data.json", "r", encoding="utf-8") as f:
        return pd.DataFrame(json.load(f)["all"])


# =========================
# HARD FILTER
# =========================

def hard_filter(df):
    df = df.copy()

    df = df[df["volume"] > 0]
    df = df[df["close"] > 0]

    if "change_rate" in df.columns:
        df["change_rate"] = pd.to_numeric(df["change_rate"], errors="coerce").fillna(0)
        df = df[df["change_rate"].abs() < 30]

    return df.reset_index(drop=True)


# =========================
# 🔥 핵심: 당일 확률 필터
# =========================

def premarket_filter(df):
    df = df.copy()

    # 과열 제거
    df = df[df["change_rate"] < 6]

    # 너무 약한 종목 제거
    df = df[df["change_rate"] > -3]

    # 거래량 하위 제거
    if "volume" in df.columns:
        th = df["volume"].quantile(0.3)
        df = df[df["volume"] > th]

    print(f"[PRE-FILTER] {len(df)} 종목")
    return df.reset_index(drop=True)


# =========================
# 🔥 당일 확률 점수
# =========================

def apply_setup_score(df):
    df = df.copy()

    df["setup_score"] = 0

    # 수급 + 상승
    df["setup_score"] += np.where(
        (df["flow"] > 0) & (df["momentum"] > 0), 1, 0
    )

    # 뉴스 + 상승
    df["setup_score"] += np.where(
        (df["event"] > 0) & (df["momentum"] > 0), 1, 0
    )

    # 과열 감점
    df["setup_score"] -= np.where(df["change_rate"] > 5, 1, 0)

    return df


# =========================
# 설명 생성 (상품 핵심)
# =========================

def build_reason(df):
    df = df.copy()

    def make_reason(row):
        if row["flow"] > 0 and row["momentum"] > 0:
            return "수급 + 상승 모멘텀"
        elif row["event"] > 0:
            return "뉴스/이슈 반영"
        else:
            return "기술적 반등 후보"

    df["reason"] = df.apply(make_reason, axis=1)
    return df


# =========================
# PIPELINE
# =========================

def run_pipeline():

    print("[PIPELINE START]")

    df = load_data()

    # code 정규화
    df["code"] = df["code"].astype(str).str.zfill(6)

    # 필터
    df = hard_filter(df)

    regime = get_regime()

    news_data = fetch_news()

    # SIGNAL
    df = compute_signal(df, news_data, regime)

    # 🔥 핵심 필터
    df = premarket_filter(df)

    # 점수 보정
    df = apply_setup_score(df)

    # 최종 점수
    df["final_score"] = df["score"] + df["setup_score"] * 0.2

    # 정렬
    df = df.sort_values("final_score", ascending=False)

    # 설명 추가
    df = build_reason(df)

    # normalize
    df = normalize_df(df)

    top10 = df.head(TOP_N)
    top3 = top10.head(3)
    top7 = top10.tail(7)

    result = {
        "date": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
        "regime": regime,
        "top3": top3.to_dict("records"),
        "top7": top7.to_dict("records"),
        "top10": top10.to_dict("records"),
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[DONE] TOP10 생성 완료")

    append_history(top10)
