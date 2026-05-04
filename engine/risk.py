import numpy as np

def apply_risk_filter(df):

    df = df.copy()

    # 필수 컬럼 안전 처리
    if "close" not in df.columns:
        df["close"] = 0
    if "volume" not in df.columns:
        df["volume"] = 0

    df["value"] = df["close"] * df["volume"]

    # 하위 유동성 제거 (30%)
    threshold = df["value"].quantile(0.3)
    df = df[df["value"] > threshold]

    # 이상값 제거
    df = df.replace([np.inf, -np.inf], 0)

    return df
