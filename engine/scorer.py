import os
import numpy as np
import pandas as pd

# =========================
# HISTORY PATH (참조용)
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(BASE_DIR, "api/_private/history.csv")

TOP_N = 10  # top10 기준


# =========================
# 자동 가중치 계산
# =========================

def get_weights() -> dict:
    """
    history 축적량에 따라 가중치 자동 조절
    
    Phase 1 (0~13일):   초기 — momentum/volume 중심, news 최소
    Phase 2 (14~29일):  중기 — volume_ratio 투입, news 비중 상향
    Phase 3 (30일~):    안정 — 전 지표 균형
    """

    history_days = 0

    if os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0:
        try:
            hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})
            if "date" in hist.columns:
                history_days = hist["date"].nunique()
        except Exception:
            history_days = 0

    # Phase 1 — 초기 (2주 미만)
    if history_days < 14:
        print(f"[SCORER] Phase 1 — 초기 ({history_days}일) | momentum 0.50 / volume 0.40 / news 0.10")
        return {
            "momentum":     0.50,
            "volume":       0.40,
            "volume_ratio": 0.00,
            "news":         0.10,
        }

    # Phase 2 — 중기 (2주~1개월)
    elif history_days < 30:
        print(f"[SCORER] Phase 2 — 중기 ({history_days}일) | momentum 0.45 / volume_ratio 0.30 / volume 0.05 / news 0.20")
        return {
            "momentum":     0.45,
            "volume":       0.05,
            "volume_ratio": 0.30,
            "news":         0.20,
        }

    # Phase 3 — 안정 (1개월 이상)
    else:
        print(f"[SCORER] Phase 3 — 안정 ({history_days}일) | momentum 0.35 / volume_ratio 0.30 / news 0.35")
        return {
            "momentum":     0.35,
            "volume":       0.00,
            "volume_ratio": 0.30,
            "news":         0.35,
        }


# =========================
# VOLUME RATIO 계산
# =========================

def build_volume_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """
    거래량비율 = 오늘 volume / 20일 평균 volume
    history.csv 에서 과거 volume 로드하여 계산
    데이터 부족 시 raw volume 사용
    """

    df = df.copy()

    if not (os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0):
        df["volume_ratio"] = 0.0
        return df

    try:
        hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})

        if "volume" not in hist.columns or "date" not in hist.columns:
            df["volume_ratio"] = 0.0
            return df

        # 종목별 평균 거래량 (최근 20일)
        hist = hist.sort_values("date")
        avg_vol = (
            hist.groupby("code")["volume"]
            .apply(lambda x: x.tail(20).mean())
            .reset_index()
        )
        avg_vol.columns = ["code", "avg_volume"]

        df = df.merge(avg_vol, on="code", how="left")

        # volume_ratio 계산 (평균 0이면 1로 처리)
        df["avg_volume"] = df["avg_volume"].fillna(0)
        df["volume_ratio"] = df.apply(
            lambda r: r["volume"] / r["avg_volume"] if r["avg_volume"] > 0 else 1.0,
            axis=1
        )
        df["volume_ratio"] = df["volume_ratio"].replace([np.inf, -np.inf], 1.0).fillna(1.0)
        df = df.drop(columns=["avg_volume"])

        print("[SCORER] volume_ratio 계산 완료")

    except Exception as e:
        print(f"[SCORER] volume_ratio 계산 실패: {e}")
        df["volume_ratio"] = 0.0

    return df


# =========================
# SCORER
# =========================

def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Choonsimi Scorer v3 — 자동 가중치 조절
    """

    df = df.copy()

    # 1) 가중치 자동 결정
    w = get_weights()

    # 2) 필수 컬럼 보정
    for c in ["news", "momentum", "volume", "volume_ratio"]:
        if c not in df.columns:
            df[c] = 0

    # 3) volume_ratio 필요 시 계산
    if w["volume_ratio"] > 0:
        df = build_volume_ratio(df)

    # 4) NaN / inf 방지
    for c in ["news", "momentum", "volume", "volume_ratio"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c] = df[c].replace([np.inf, -np.inf], 0).fillna(0)

    # 5) 각 지표 min-max 정규화 (0~1, 스케일 통일)
    def minmax(series):
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series(0.0, index=series.index)
        return (series - mn) / (mx - mn)

    df["_news"]         = minmax(df["news"])
    df["_momentum"]     = minmax(df["momentum"])
    df["_volume"]       = minmax(df["volume"])
    df["_volume_ratio"] = minmax(df["volume_ratio"])

    # 6) 가중합
    df["score"] = (
        df["_momentum"]     * w["momentum"]     +
        df["_volume"]       * w["volume"]        +
        df["_volume_ratio"] * w["volume_ratio"]  +
        df["_news"]         * w["news"]
    )

    # 7) 임시 컬럼 제거
    df = df.drop(columns=["_news", "_momentum", "_volume", "_volume_ratio"])

    # 8) 안정화
    df["score"] = df["score"].replace([np.inf, -np.inf], 0).fillna(0)

    return df
