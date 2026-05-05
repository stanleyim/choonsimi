"""
engine/regime.py — v1.0
REGIME 판단: KOSPI 추세(가격) + KIS 수급(돈 흐름) 2축 결합
PRIMARY: FDR KS11 (MA20/MA60)
FALLBACK: market_flow.json (누적 데이터)
OUTPUT: UPTREND / SIDEWAY / DOWNTREND + override 지원
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_FLOW_PATH = os.path.join(BASE_DIR, "market_flow.json")
OVERRIDE_PATH    = os.path.join(BASE_DIR, "regime_override.json")


# =========================
# KOSPI 추세 판단 (60%)
# =========================

def get_kospi_trend() -> str:
    """
    FDR KS11 → MA20 / MA60 계산 → 추세 판단
    실패 시 market_flow.json fallback
    """

    # PRIMARY: FDR
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KS11", start="2024-01-01")

        if df is None or len(df) < 60:
            raise ValueError("KS11 데이터 부족")

        close = df["Close"].astype(float)
        ma20  = close.rolling(20).mean()
        ma60  = close.rolling(60).mean()

        last_ma20 = ma20.iloc[-1]
        last_ma60 = ma60.iloc[-1]

        # slope: 최근 5일 MA20 방향
        slope = ma20.iloc[-1] - ma20.iloc[-6]

        ratio = (last_ma20 - last_ma60) / last_ma60 * 100  # %

        if last_ma20 > last_ma60 and slope > 0:
            trend = "UPTREND"
        elif last_ma20 < last_ma60:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAY"

        print(f"[REGIME] KOSPI MA20={last_ma20:.1f} MA60={last_ma60:.1f} gap={ratio:.2f}% → {trend}")
        return trend

    except Exception as e:
        print(f"[REGIME] FDR 실패 → fallback: {e}")
        return get_kospi_trend_from_flow()


def get_kospi_trend_from_flow() -> str:
    """
    FALLBACK: market_flow.json에서 KOSPI 누적 → MA 계산
    kospi 컬럼 없으면 SIDEWAY 반환
    """
    try:
        if not os.path.exists(MARKET_FLOW_PATH):
            print("[REGIME] market_flow.json 없음 → SIDEWAY")
            return "SIDEWAY"

        with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data)

        if "kospi" not in df.columns or len(df) < 20:
            print("[REGIME] kospi 컬럼 없음 또는 데이터 부족 → SIDEWAY")
            return "SIDEWAY"

        df = df.sort_values("date").reset_index(drop=True)
        close = df["kospi"].astype(float)

        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(min(60, len(close))).mean().iloc[-1]
        slope = close.iloc[-1] - close.iloc[-6] if len(close) >= 6 else 0

        if ma20 > ma60 and slope > 0:
            trend = "UPTREND"
        elif ma20 < ma60:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAY"

        print(f"[REGIME] fallback KOSPI trend → {trend}")
        return trend

    except Exception as e:
        print(f"[REGIME] fallback 실패 → SIDEWAY: {e}")
        return "SIDEWAY"


# =========================
# 수급 판단 (40%)
# =========================

def get_flow_signal() -> str:
    """
    market_flow.json 최근 5일 외국인+기관 순매수 합산
    POSITIVE / NEUTRAL / NEGATIVE
    """
    try:
        if not os.path.exists(MARKET_FLOW_PATH):
            return "NEUTRAL"

        with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
        recent = df.tail(5)

        foreign_sum = recent["foreign_net"].sum()
        inst_sum    = recent["inst_net"].sum()
        combined    = foreign_sum * 0.7 + inst_sum * 0.3

        # 양쪽 방향 체크
        foreign_pos = foreign_sum > 0
        inst_pos    = inst_sum > 0

        if foreign_pos and inst_pos:
            signal = "POSITIVE"
        elif not foreign_pos and not inst_pos:
            signal = "NEGATIVE"
        else:
            signal = "NEUTRAL"  # 외국인 vs 기관 상쇄

        print(f"[REGIME] flow → 외국인 {'+' if foreign_pos else '-'} / 기관 {'+' if inst_pos else '-'} → {signal}")
        return signal

    except Exception as e:
        print(f"[REGIME] flow 판단 실패 → NEUTRAL: {e}")
        return "NEUTRAL"


# =========================
# REGIME 최종 결합
# =========================

def get_regime() -> str:
    """
    최종 REGIME 판단
    1. override 체크 (FOMC / 블랙스완 등)
    2. KOSPI 추세(60%) + 수급(40%) 결합
    """

    # override 체크
    if os.path.exists(OVERRIDE_PATH):
        try:
            with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
                override = json.load(f)
            if override.get("active", False):
                regime = override.get("regime", "SIDEWAY").upper()
                reason = override.get("reason", "manual override")
                print(f"[REGIME] ⚠️ OVERRIDE 적용: {regime} ({reason})")
                return regime
        except:
            pass

    # 자동 판단
    trend  = get_kospi_trend()   # UPTREND / SIDEWAY / DOWNTREND
    flow   = get_flow_signal()   # POSITIVE / NEUTRAL / NEGATIVE

    # 결합 규칙 (60% + 40%)
    if trend == "UPTREND" and flow == "POSITIVE":
        regime = "UPTREND"
    elif trend == "DOWNTREND" or flow == "NEGATIVE":
        regime = "DOWNTREND"
    else:
        regime = "SIDEWAY"

    print(f"[REGIME] 최종: {regime} (trend={trend}, flow={flow})")
    return regime
