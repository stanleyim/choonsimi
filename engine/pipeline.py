"""
engine/pipeline.py — v2.0 FINAL
구조: DATA → HARD FILTER → SIGNAL ENGINE → REGIME → RANKING → OUTPUT
"""

import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

from engine.normalizer import normalize_df
from engine.signal import compute_signal
from engine.regime import get_regime
from engine.history import append_history, HISTORY_PATH

from news_fetch import run as fetch_news

TOP_N = 10


# =========================
# LOAD DATA
# =========================

def load_data() -> pd.DataFrame:
    with open("data.json", "r", encoding="utf-8") as f:
        return pd.DataFrame(json.load(f)["all"])


def load_market_flow() -> pd.DataFrame:
    try:
        with open("market_flow.json", "r", encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))
    except:
        return pd.DataFrame()


# =========================
# MARKET CONTEXT (시장 심리)
# =========================

def build_market_context(flow_df: pd.DataFrame, regime: str) -> dict:
    if flow_df.empty:
        return {"label": regime, "score": 0.0, "foreign_5d": 0, "inst_5d": 0}

    flow_df = flow_df.copy().sort_values("date").reset_index(drop=True)
    short = flow_df.tail(5)

    foreign_5d = int(short["foreign_net"].sum())
    inst_5d    = int(short["inst_net"].sum())

    combined = foreign_5d * 0.7 + inst_5d * 0.3
    max_val = flow_df[["foreign_net", "inst_net"]].abs().max().max()
    score = float(np.clip(combined / (max_val * 10), -1.0, 1.0)) if max_val > 0 else 0.0

    return {
        "label":      regime,
        "score":      round(score, 4),
        "foreign_5d": foreign_5d,
        "inst_5d":    inst_5d,
    }


# =========================
# HARD FILTER
# =========================

def hard_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    쓰레기 종목 사전 제거
    - 거래량 0
    - 종가 0 이하
    - 비정상 급등락 (±30% 초과 — 서킷브레이커 / 관리종목)
    """
    before = len(df)

    df = df[df["volume"] > 0]
    df = df[df["close"]  > 0]

    if "change_rate" in df.columns:
        df["change_rate"] = pd.to_numeric(df["change_rate"], errors="coerce").fillna(0)
        df = df[df["change_rate"].abs() < 30]

    after = len(df)
    print(f"[FILTER] {before} → {after}종목 ({before - after}개 제거)")
    return df.reset_index(drop=True)


# =========================
# PIPELINE
# =========================

def run_pipeline():

    # 1. 데이터 로드
    df = load_data()

    # 2. code 정규화
    df["code"] = (
        df["code"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )

    # 3. HARD FILTER
    df = hard_filter(df)

    # 4. REGIME 판단
    flow_df = load_market_flow()
    regime  = get_regime()
    market_context = build_market_context(flow_df, regime)
    print(f"[PIPELINE] REGIME={regime}")

    # 5. 뉴스 수집
    news_data = fetch_news()

    # 6. SIGNAL ENGINE (FLOW + MOMENTUM + EVENT)
    df = compute_signal(df, news_data, regime)

    # 7. normalize (중복 제거 + 정렬)
    df = normalize_df(df)

    # 8. ranking
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    top10 = df.head(TOP_N)
    top3  = top10.head(3)   # high conviction
    rest7 = top10.tail(7)   # stable picks

    # 9. output
    result = {
        "date":           datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
        "regime":         regime,
        "market_context": market_context,
        "top3":           top3.to_dict("records"),
        "top7":           rest7.to_dict("records"),
        "top10":          top10.to_dict("records"),
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] regime={regime} | top10 저장 완료")

    # 10. history 누적
    append_history(top10)
