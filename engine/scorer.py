import numpy as np
import pandas as pd


def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Choonsimi Scorer v2
    
    ✅ Fix #1 반영: news는 pipeline에서 이미 정제된 값 사용
    ✅ Fix #2 반영: momentum은 실제 change_rate 또는 history 기반
    ✅ Fix #3 반영: flow 제거 (시장 전체 스칼라 → 종목 차별화 불가)
                   market_context로 별도 출력
    ✅ Fix #4 반영: pipeline에서 compute_score → normalize_df 순서로 호출
    
    가중치: news 0.40 / momentum 0.35 / volume 0.25
    """

    df = df.copy()

    # 1) 필수 컬럼 안전 보정 (flow 제거)
    for c in ["news", "momentum", "volume"]:
        if c not in df.columns:
            df[c] = 0

    # 2) NaN / inf 방지
    df[["news", "momentum", "volume"]] = df[
        ["news", "momentum", "volume"]
    ].replace([np.inf, -np.inf], 0).fillna(0)

    # 3) raw score 계산 (가중합)
    # flow 제거 후 재분배: news +0.10 / momentum +0.05 / volume +0.05
    df["score"] = (
        df["news"]     * 0.40 +
        df["momentum"] * 0.35 +
        df["volume"]   * 0.25
    )

    # 4) 스케일 압축 — 부호 보존 log 변환 (RuntimeWarning 완전 제거)
    df["score"] = np.sign(df["score"]) * np.log1p(np.abs(df["score"]))

    # 5) 안정화
    df["score"] = df["score"].replace([np.inf, -np.inf], 0).fillna(0)

    return df
