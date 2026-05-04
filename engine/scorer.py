import numpy as np

def compute_score(df):

    df = df.copy()

    # 기본 안전값
    for c in ["news", "momentum", "volume", "flow"]:
        if c not in df.columns:
            df[c] = 0

    df["score"] = (
        df["news"] * 0.3 +
        df["momentum"] * 0.3 +
        df["volume"] * 0.2 +
        df["flow"] * 0.2
    )

    df["score"] = df["score"].replace([np.inf, -np.inf], 0)

    return df
