import json
import os

# ========================
# PATH (ROOT FIXED)
# ========================
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(ROOT, "portfolio.json")


# ========================
# BUILD PORTFOLIO
# ========================
def build_portfolio(df, top_n=10):
    df = df.sort_values("score", ascending=False).head(top_n)
    return df[["code", "score", "weight"]].to_dict("records")


# ========================
# LOAD PREVIOUS
# ========================
def load_prev_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


# ========================
# SAVE PORTFOLIO
# ========================
def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)


# ========================
# COMPARE PORTFOLIO
# ========================
def compare_portfolio(prev, new):
    p1 = {x["code"] for x in prev}
    p2 = {x["code"] for x in new}
    return list(p2 - p1), list(p1 - p2)
