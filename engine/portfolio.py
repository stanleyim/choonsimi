import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(ROOT, "portfolio.json")


def build_portfolio(df, top_n=10):
    required_cols = {"code", "score", "weight"}

    if not required_cols.issubset(df.columns):
        raise ValueError(f"Missing columns: {required_cols - set(df.columns)}")

    df = df.dropna(subset=["score"])
    df = df.sort_values("score", ascending=False).head(top_n)

    return df[["code", "score", "weight"]].to_dict("records")


def load_prev_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []


def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)


def compare_portfolio(prev, new):
    p1 = {x.get("code") for x in prev if "code" in x}
    p2 = {x.get("code") for x in new if "code" in x}

    return list(p2 - p1), list(p1 - p2)
