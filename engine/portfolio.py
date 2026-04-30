import json, os

PORTFOLIO_FILE = "portfolio.json"

def build_portfolio(df, top_n=10):
    df = df.sort_values("score", ascending=False).head(top_n)
    return df[["code", "score", "weight"]].to_dict("records")


def load_prev_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        return json.load(open(PORTFOLIO_FILE))
    return []


def save_portfolio(p):
    json.dump(p, open(PORTFOLIO_FILE, "w"), indent=2)


def compare_portfolio(prev, new):
    p1 = {x["code"] for x in prev}
    p2 = {x["code"] for x in new}
    return list(p2 - p1), list(p1 - p2)
