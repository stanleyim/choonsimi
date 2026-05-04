import json
import pandas as pd

from engine.normalizer import normalize_df
from engine.scorer import compute_score
from engine.risk import apply_risk_filter

TOP_N = 10

def load_data():
    with open("data.json", "r", encoding="utf-8") as f:
        return pd.DataFrame(json.load(f)["all"])


def run_pipeline():

    # 1. load
    df = load_data()

    # 2. normalize (code + dedup)
    df = normalize_df(df)

    # 3. feature score
    df = compute_score(df)

    # 4. risk filter
    df = apply_risk_filter(df)

    # 5. ranking
    df = df.sort_values("score", ascending=False)

    top10 = df.head(TOP_N)
    top3  = top10.head(3)

    result = {
        "top10": top10.to_dict("records"),
        "top3": top3.to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_pipeline()
