import numpy as np
import pandas as pd

def compute_score(df: pd.DataFrame) -> pd.DataFrame:

    df = df.copy()

    # 1) 필수 컬럼 안전 보정
    for c in ["news", "momentum", "volume", "flow"]:
        if c not in df.columns:
            df[c] = 0

    # 2) NaN / inf 방지
    df[["news", "momentum", "volume", "flow"]] = df[
        ["news", "momentum", "volume", "flow"]
    ].replace([np.inf, -np.inf], 0).fillna(0)

    # 3) raw score 계산 (가중합)
    df["score"] = (
        df["news"] * 0.3 +
        df["momentum"] * 0.3 +
        df["volume"] * 0.2 +
        df["flow"] * 0.2
    )

    # 4) 스케일 압축 (핵심 수정)
    df["score"] = np.log1p(df["score"])

    # 5) 안정화
    df["score"] = df["score"].replace([np.inf, -np.inf], 0).fillna(0)

    return df
