import json

def build_portfolio(df, top_n=10):
    return df.sort_values("score", ascending=False).head(top_n).to_dict("records")


def save_portfolio(port):
    with open("portfolio.json", "w") as f:
        json.dump(port, f, ensure_ascii=False, indent=2)


def load_prev_portfolio():
    try:
        with open("portfolio.json") as f:
            return json.load(f)
    except:
        return []


def compare_portfolio(prev, new):
    prev_codes = set([x["code"] for x in prev])
    new_codes = set([x["code"] for x in new])

    add = list(new_codes - prev_codes)
    rem = list(prev_codes - new_codes)

    return add, rem
