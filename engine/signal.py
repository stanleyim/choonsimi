"""
engine/signal.py — v1.0
SIGNAL ENGINE: 3개 핵심 팩터만
FLOW     0.50 (KIS 종목별 수급)
MOMENTUM 0.30 (KRX 등락률)
EVENT    0.20 (DART + NEWS 합산)
volume 제거 (bias 존재)
phase system 제거 (복잡도 제거)
REGIME 보정만 유지
"""

import os
import json
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_FLOW_PATH   = os.path.join(BASE_DIR, "stock_flow.json")
FUNDAMENTAL_PATH  = os.path.join(BASE_DIR, "fundamental.json")


# =========================
# 기본 가중치
# =========================

BASE_WEIGHTS = {
    "flow":     0.50,
    "momentum": 0.30,
    "event":    0.20,
}

# REGIME 보정
REGIME_ADJUST = {
    "UPTREND":   {"momentum": +0.10, "flow": 0,     "event": -0.10},
    "SIDEWAY":   {"flow": +0.10,     "momentum": 0,  "event": -0.10},
    "DOWNTREND": {"event": +0.10,    "flow": -0.05,  "momentum": -0.05},
}


def get_weights(regime: str) -> dict:
    """REGIME에 따른 최종 가중치 계산"""
    w = BASE_WEIGHTS.copy()
    adj = REGIME_ADJUST.get(regime, {})
    for k, v in adj.items():
        w[k] = round(w.get(k, 0) + v, 4)
    # 합산 1.0 정규화
    total = sum(w.values())
    if total > 0:
        w = {k: round(v / total, 4) for k, v in w.items()}
    print(f"[SIGNAL] 가중치 (regime={regime}): {w}")
    return w


# =========================
# FLOW FACTOR (KIS 종목별)
# =========================

def build_flow_factor(df: pd.DataFrame) -> pd.DataFrame:
    """
    stock_flow.json → 종목별 외국인+기관 순매수 합산
    없으면 flow=0
    """
    df = df.copy()

    if not os.path.exists(STOCK_FLOW_PATH):
        df["flow"] = 0.0
        print("[SIGNAL] stock_flow.json 없음 → flow=0")
        return df

    try:
        with open(STOCK_FLOW_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)

        flow_df = pd.DataFrame(raw)

        if flow_df.empty or "code" not in flow_df.columns:
            df["flow"] = 0.0
            return df

        # 최근 5일 종목별 가중 합산
        flow_df["code"] = flow_df["code"].astype(str).str.zfill(6)
        flow_df = flow_df.sort_values("date")

        def calc(group):
            s = group.tail(5)
            m = group.tail(10)
            l = group
            return (
                (s["foreign_net"].sum() + s["inst_net"].sum()) * 0.5 +
                (m["foreign_net"].sum() + m["inst_net"].sum()) * 0.3 +
                (l["foreign_net"].sum() + l["inst_net"].sum()) * 0.2
            )

        flow_score = flow_df.groupby("code").apply(calc).reset_index()
        flow_score.columns = ["code", "flow"]

        df = df.merge(flow_score, on="code", how="left")
        df["flow"] = df["flow"].fillna(0.0)

        print(f"[SIGNAL] flow factor 적용: {(df['flow'] != 0).sum()}종목")

    except Exception as e:
        print(f"[SIGNAL] flow 계산 실패: {e}")
        df["flow"] = 0.0

    return df


# =========================
# MOMENTUM FACTOR (KRX 등락률)
# =========================

def build_momentum_factor(df: pd.DataFrame) -> pd.DataFrame:
    """
    change_rate 컬럼 직접 사용
    없으면 0
    """
    df = df.copy()

    for col in ["change_rate", "chg_rate", "pct_change", "rate"]:
        if col in df.columns:
            df["momentum"] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            print(f"[SIGNAL] momentum: {col} 직접 사용")
            return df

    df["momentum"] = 0.0
    print("[SIGNAL] momentum: fallback 0")
    return df


# =========================
# EVENT FACTOR (DART + NEWS 합산)
# =========================

def build_event_factor(df: pd.DataFrame, news_data: list) -> pd.DataFrame:
    """
    DART 재무 점수 + NEWS 감성 점수 → EVENT 합산
    """
    df = df.copy()
    df["event"] = 0.0

    # NEWS 점수
    if news_data:
        news_df = pd.DataFrame(news_data)
        if "score" in news_df.columns:
            news_df = news_df.rename(columns={"score": "news_score"})
        if "news_score" in news_df.columns:
            news_df["code"] = news_df["code"].astype(str).str.zfill(6)
            df = df.merge(news_df[["code", "news_score"]], on="code", how="left")
            df["event"] += df["news_score"].fillna(0)
            df = df.drop(columns=["news_score"])

    # DART 재무 점수
    if os.path.exists(FUNDAMENTAL_PATH):
        try:
            with open(FUNDAMENTAL_PATH, "r", encoding="utf-8") as f:
                fund = json.load(f)

            stocks = fund.get("stocks", [])
            if stocks:
                fund_df = pd.DataFrame(stocks)
                fund_df["code"] = fund_df["code"].astype(str).str.zfill(6)

                # 재무 점수 계산
                # op_growth > 0 → +1 / roe > 10 → +1 / debt_ratio < 200 → +1
                def fund_score(row):
                    s = 0
                    if row.get("op_growth", 0) > 0:   s += 1
                    if row.get("roe", 0) > 10:         s += 1
                    if 0 < row.get("debt_ratio", 999) < 200: s += 1
                    return float(s)

                fund_df["fund_score"] = fund_df.apply(fund_score, axis=1)
                df = df.merge(fund_df[["code", "fund_score"]], on="code", how="left")
                df["event"] += df["fund_score"].fillna(0)
                df = df.drop(columns=["fund_score"])
                print(f"[SIGNAL] DART 재무 점수 적용: {len(fund_df)}종목")

        except Exception as e:
            print(f"[SIGNAL] DART 로드 실패: {e}")

    return df


# =========================
# SIGNAL ENGINE 메인
# =========================

def compute_signal(df: pd.DataFrame, news_data: list, regime: str) -> pd.DataFrame:
    """
    최종 SIGNAL 계산
    1. 팩터 빌드
    2. 가중치 적용 (REGIME 보정)
    3. min-max 정규화
    4. 최종 score 계산
    """

    df = df.copy()

    # 팩터 빌드
    df = build_flow_factor(df)
    df = build_momentum_factor(df)
    df = build_event_factor(df, news_data)

    # NaN / inf 방지
    for c in ["flow", "momentum", "event"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c] = df[c].replace([np.inf, -np.inf], 0).fillna(0)

    # min-max 정규화 (스케일 통일)
    def minmax(series):
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series(0.0, index=series.index)
        return (series - mn) / (mx - mn)

    df["_flow"]     = minmax(df["flow"])
    df["_momentum"] = minmax(df["momentum"])
    df["_event"]    = minmax(df["event"])

    # REGIME 보정 가중치
    w = get_weights(regime)

    # 최종 score
    df["score"] = (
        df["_flow"]     * w["flow"]     +
        df["_momentum"] * w["momentum"] +
        df["_event"]    * w["event"]
    )

    # 보너스 시그널 (중첩 신호 강화)
    bonus = pd.Series(0.0, index=df.index)

    # 외국인+기관 동시 순매수 AND momentum 양수
    if "flow" in df.columns and "momentum" in df.columns:
        bonus += np.where((df["flow"] > 0) & (df["momentum"] > 0), 0.10, 0)

    # 뉴스+등락률 동시 양수
    if "event" in df.columns and "momentum" in df.columns:
        bonus += np.where((df["event"] > 0) & (df["momentum"] > 0), 0.05, 0)

    df["score"] = df["score"] + bonus

    # 임시 컬럼 제거
    df = df.drop(columns=["_flow", "_momentum", "_event"])

    # 안정화
    df["score"] = df["score"].replace([np.inf, -np.inf], 0).fillna(0)

    return df
