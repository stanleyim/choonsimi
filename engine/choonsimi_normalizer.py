import pandas as pd

class ChoonsimiEngineV1:

    def run(self, df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy()

        # 1) code 6자리 고정
        df["code"] = (
            df["code"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(6)
        )

        # 2) score 숫자 변환
        df["score"] = pd.to_numeric(df["score"], errors="coerce")
        df = df.dropna(subset=["score"])

        # 3) 소수점 안정화
        df["score"] = df["score"].round(6)

        # 4) 중복 제거 (score 높은 것 유지)
        df = df.sort_values("score", ascending=False)
        df = df.drop_duplicates(subset=["code"], keep="first")

        # 5) 최종 정렬
        df = df.sort_values("score", ascending=False).reset_index(drop=True)

        return df
