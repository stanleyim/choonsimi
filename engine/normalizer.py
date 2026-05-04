import pandas as pd


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Choonsimi Normalizer v1 (production safe)
    - code 표준화 (6자리 고정)
    - score 정제
    - 중복 제거 (code 기준)
    - index 안정화
    """

    df = df.copy()

    # 1) code 6자리 고정 (핵심)
    if "code" in df.columns:
        df["code"] = (
            df["code"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"[^0-9]", "", regex=True)  # 혹시 섞인 문자 제거
            .str.zfill(6)
        )

    # 2) score 숫자 변환 + NaN 제거
    if "score" in df.columns:
        df["score"] = pd.to_numeric(df["score"], errors="coerce")
        df = df.dropna(subset=["score"])

        # 3) 소수점 안정화 (rank 유지용)
        df["score"] = df["score"].round(6)

    # 4) 정렬 (score 기준)
    if "score" in df.columns:
        df = df.sort_values("score", ascending=False)

    # 5) 중복 제거 (code 기준, 상위 score 유지)
    if "code" in df.columns:
        df = df.drop_duplicates(subset=["code"], keep="first")

    # 6) index reset
    df = df.reset_index(drop=True)

    return df
