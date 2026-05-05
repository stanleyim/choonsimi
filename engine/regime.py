"""
engine/regime.py — v3.0
PRIMARY:   KS11 MA20/MA60 + slope (confidence 1.0)
FALLBACK1: market_flow.json kospi (confidence 0.6)
FALLBACK2: last_valid_regime cache (confidence 0.3) ← "보존"으로 교체
+ regime confidence score 추가
"""

import os
import json
import numpy as np
import pandas as pd

BASE_DIR          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_FLOW_PATH  = os.path.join(BASE_DIR, "market_flow.json")
OVERRIDE_PATH     = os.path.join(BASE_DIR, "regime_override.json")
REGIME_CACHE_PATH = os.path.join(BASE_DIR, "regime_cache.json")  # ✅ 신규


# =========================
# CACHE (last known regime)
# =========================

def load_cached_regime() -> dict:
    """마지막 유효 regime 로드"""
    try:
        if os.path.exists(REGIME_CACHE_PATH):
            with open(REGIME_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            regime = cache.get("regime", "SIDEWAY")
            date   = cache.get("date", "unknown")
            print(f"[REGIME] cache 로드: {regime} ({date})")
            return {"regime": regime, "confidence": 0.3}
    except Exception as e:
        print(f"[REGIME] cache 로드 실패: {e}")
    return {"regime": "SIDEWAY", "confidence": 0.3}


def save_regime_cache(regime: str):
    """유효 regime 캐시 저장"""
    try:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        with open(REGIME_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"regime": regime, "date": today}, f)
    except Exception as e:
        print(f"[REGIME] cache 저장 실패: {e}")


# =========================
# PRIMARY: KS11 MA20/MA60
# =========================

def get_kospi_trend_primary() -> dict:
    """KS11 MA20/MA60 + slope → confidence 1.0"""
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
        ratio     = (last_ma20 - last_ma60) / last_ma60 * 100

        if last_ma20 > last_ma60 and slope > 0:
            trend = "UPTREND"
        elif last_ma20 < last_ma60:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAY"

        print(f"[REGIME] PRIMARY KS11 MA20={last_ma20:.1f} MA60={last_ma60:.1f} gap={ratio:.2f}% → {trend} (confidence=1.0)")
        return {"regime": trend, "confidence": 1.0}

    except Exception as e:
        print(f"[REGIME] PRIMARY 실패: {e}")
        return None


# =========================
# FALLBACK1: market_flow kospi
# =========================

def get_kospi_trend_fallback1() -> dict:
    """market_flow.json kospi 컬럼 MA → confidence 0.6"""
    try:
        if not os.path.exists(MARKET_FLOW_PATH):
            return None

        with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)

        if "kospi" not in df.columns or len(df) < 20:
            return None

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

        print(f"[REGIME] FALLBACK1 market_flow kospi → {trend} (confidence=0.6)")
        return {"regime": trend, "confidence": 0.6}

    except Exception as e:
        print(f"[REGIME] FALLBACK1 실패: {e}")
        return None


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

        df      = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
        recent  = df.tail(5)
        foreign = recent["foreign_net"].sum() > 0
        inst    = recent["inst_net"].sum() > 0

        if foreign and inst:
            signal = "POSITIVE"
        elif not foreign and not inst:
            signal = "NEGATIVE"
        else:
            signal = "NEUTRAL"

        print(f"[REGIME] flow 외국인 {'+' if foreign else '-'} / 기관 {'+' if inst else '-'} → {signal}")
        return signal

    except Exception as e:
        print(f"[REGIME] flow 판단 실패 → NEUTRAL: {e}")
        return "NEUTRAL"


# =========================
# REGIME 최종 결합
# =========================

def get_regime() -> str:
    """
    최종 REGIME 판단 + confidence score
    override → PRIMARY → FALLBACK1 → FALLBACK2(cache 보존)
    항상 UPTREND/SIDEWAY/DOWNTREND 반환
    """
    try:
        # override 체크
        if os.path.exists(OVERRIDE_PATH):
            with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
                override = json.load(f)
            if override.get("active", False):
                regime = override.get("regime", "SIDEWAY").upper()
                reason = override.get("reason", "manual")
                print(f"[REGIME] ⚠️ OVERRIDE: {regime} ({reason}) confidence=1.0")
                save_regime_cache(regime)
                return regime

        # PRIMARY
        result = get_kospi_trend_primary()

        # FALLBACK1
        if result is None:
            result = get_kospi_trend_fallback1()

        # FALLBACK2 — ✅ 생성 → 보존 (last known regime cache)
        if result is None:
            result = load_cached_regime()
            print(f"[REGIME] FALLBACK2 cache 보존: {result['regime']} (confidence={result['confidence']})")

        trend      = result["regime"]
        confidence = result["confidence"]

        # flow 결합 (confidence 1.0일 때만 적용)
        if confidence >= 0.6:
            flow = get_flow_signal()
            if trend == "UPTREND" and flow == "NEGATIVE":
                trend = "SIDEWAY"
                print(f"[REGIME] flow 역행 → UPTREND → SIDEWAY 조정")
            elif trend == "SIDEWAY" and flow == "POSITIVE":
                trend = "UPTREND"
                print(f"[REGIME] flow 확인 → SIDEWAY → UPTREND 상향")
        else:
            print(f"[REGIME] confidence={confidence} 낮음 → flow 결합 생략")

        print(f"[REGIME] 최종: {trend} (confidence={confidence})")

        # 유효 regime 캐시 저장 (confidence 0.6 이상만)
        if confidence >= 0.6:
            save_regime_cache(trend)

        return trend

    except Exception as e:
        print(f"[REGIME] 전체 실패 → cache 보존: {e}")
        return load_cached_regime()["regime"]
