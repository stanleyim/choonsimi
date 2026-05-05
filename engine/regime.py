"""
engine/regime.py — v2.0
- bs4 완전 제거
- FDR KS11 PRIMARY
- market_flow.json FALLBACK
- KRX momentum proxy FALLBACK (bs4 없이)
- graceful degradation: 항상 REGIME 반환
"""

import os
import json
import numpy as np
import pandas as pd

BASE_DIR         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_FLOW_PATH = os.path.join(BASE_DIR, "market_flow.json")
OVERRIDE_PATH    = os.path.join(BASE_DIR, "regime_override.json")


# =========================
# KOSPI 추세 (PRIMARY)
# =========================

def get_kospi_trend() -> str:
    """
    PRIMARY: FDR KS11 MA20/MA60
    FALLBACK 1: market_flow.json kospi 컬럼
    FALLBACK 2: momentum proxy (change_rate 중앙값)
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
        slope     = ma20.iloc[-1] - ma20.iloc[-6]

        if last_ma20 > last_ma60 and slope > 0:
            trend = "UPTREND"
        elif last_ma20 < last_ma60:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAY"

        ratio = (last_ma20 - last_ma60) / last_ma60 * 100
        print(f"[REGIME] KS11 MA20={last_ma20:.1f} MA60={last_ma60:.1f} gap={ratio:.2f}% → {trend}")
        return trend

    except Exception as e:
        print(f"[REGIME] FDR 실패 → fallback1: {e}")

    # FALLBACK 1: market_flow.json kospi 컬럼
    try:
        if os.path.exists(MARKET_FLOW_PATH):
            with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)

            if "kospi" in df.columns and len(df) >= 20:
                close = df["kospi"].astype(float)
                ma20  = close.rolling(20).mean().iloc[-1]
                ma60  = close.rolling(min(60, len(close))).mean().iloc[-1]
                slope = close.iloc[-1] - close.iloc[-6] if len(close) >= 6 else 0

                if ma20 > ma60 and slope > 0:
                    trend = "UPTREND"
                elif ma20 < ma60:
                    trend = "DOWNTREND"
                else:
                    trend = "SIDEWAY"

                print(f"[REGIME] market_flow kospi fallback → {trend}")
                return trend

    except Exception as e:
        print(f"[REGIME] fallback1 실패 → fallback2: {e}")

    # FALLBACK 2: data.json change_rate 중앙값으로 momentum proxy
    try:
        data_path = os.path.join(BASE_DIR, "data.json")
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = raw.get("all", [])
            if items:
                rates = [float(i.get("change_rate", 0) or 0) for i in items]
                median_rate = float(np.median(rates))

                if median_rate > 0.5:
                    trend = "UPTREND"
                elif median_rate < -0.5:
                    trend = "DOWNTREND"
                else:
                    trend = "SIDEWAY"

                print(f"[REGIME] momentum proxy (median={median_rate:.2f}%) → {trend}")
                return trend
    except Exception as e:
        print(f"[REGIME] fallback2 실패: {e}")

    print("[REGIME] 모든 fallback 실패 → SIDEWAY")
    return "SIDEWAY"


# =========================
# 수급 판단
# =========================

def get_flow_signal() -> str:
    """market_flow.json 최근 5일 외국인+기관 → POSITIVE/NEUTRAL/NEGATIVE"""
    try:
        if not os.path.exists(MARKET_FLOW_PATH):
            return "NEUTRAL"

        with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
        recent = df.tail(5)

        foreign_pos = recent["foreign_net"].sum() > 0
        inst_pos    = recent["inst_net"].sum() > 0

        if foreign_pos and inst_pos:
            signal = "POSITIVE"
        elif not foreign_pos and not inst_pos:
            signal = "NEGATIVE"
        else:
            signal = "NEUTRAL"

        print(f"[REGIME] flow 외국인 {'+' if foreign_pos else '-'} / 기관 {'+' if inst_pos else '-'} → {signal}")
        return signal

    except Exception as e:
        print(f"[REGIME] flow 판단 실패 → NEUTRAL: {e}")
        return "NEUTRAL"


# =========================
# REGIME 최종 결합
# =========================

def get_regime() -> str:
    """
    1. override 체크 (FOMC/블랙스완 등)
    2. KOSPI trend (60%) + flow (40%) 결합
    항상 UPTREND/SIDEWAY/DOWNTREND 반환 (절대 crash 없음)
    """
    try:
        # override 체크
        if os.path.exists(OVERRIDE_PATH):
            with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
                override = json.load(f)
            if override.get("active", False):
                regime = override.get("regime", "SIDEWAY").upper()
                reason = override.get("reason", "manual")
                print(f"[REGIME] ⚠️ OVERRIDE: {regime} ({reason})")
                return regime

        # 자동 판단
        trend  = get_kospi_trend()
        flow   = get_flow_signal()

        if trend == "UPTREND" and flow in ("POSITIVE", "NEUTRAL"):
            regime = "UPTREND"
        elif trend == "DOWNTREND" or flow == "NEGATIVE":
            regime = "DOWNTREND"
        else:
            regime = "SIDEWAY"

        print(f"[REGIME] 최종: {regime} (trend={trend}, flow={flow})")
        return regime

    except Exception as e:
        print(f"[REGIME] 전체 실패 → SIDEWAY: {e}")
        return "SIDEWAY"
