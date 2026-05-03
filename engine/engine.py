"""
engine.py — v25 FINAL (PRODUCTION SIGNAL ENGINE)
"""

import json, os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "data.json")
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH = os.path.join(ROOT, "result.json")

TOP_N = 10

def zscore(s):
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    if s.std() == 0:
        return pd.Series(0, index=s.index)
    return (s - s.mean()) / s.std()

def load():
    with open(DATA_PATH) as f:
        raw = json.load(f)
    df = pd.DataFrame(raw["all"])
    return df, raw["date"], raw.get("flow_coverage", 0)

def select_universe(df):
    df["turnover"] = df["close"] * df["volume"]
    df = df[df["turnover"] > df["turnover"].quantile(0.3)]
    df = df[df["close"] > 1000]
    return df.sort_values("turnover", ascending=False).head(200)

def compute_score(df, flow_cov):

    df["flow"] = df["foreign_net"] + df["inst_net"]

    if flow_cov > 0.6:
        w_flow, w_mom = 0.7, 0.3
    elif flow_cov > 0.3:
        w_flow, w_mom = 0.5, 0.5
    else:
        w_flow, w_mom = 0.0, 1.0

    df["flow_z"] = zscore(df["flow"])
    df["mom_z"] = 0

    df["score"] = w_flow * df["flow_z"] + w_mom * df["mom_z"]

    return df

def make_reason(row):
    r = []
    if row["foreign_net"] > 0: r.append("외국인 매수")
    if row["inst_net"] > 0: r.append("기관 매수")
    if row["flow"] > 0: r.append("수급 유입")
    return r

def run():

    df, date, flow_cov = load()

    df = select_universe(df)

    df = compute_score(df, flow_cov)

    df = df[df["flow"] > 0]

    top = df.sort_values("score", ascending=False).head(TOP_N)

    output = []
    for _, r in top.iterrows():
        output.append({
            "code": r["code"],
            "score": round(r["score"], 2),
            "signal": "BUY",
            "reason": make_reason(r),
            "strategy": {
                "entry": "시가~+2%",
                "target": "+5~10%",
                "stop_loss": "-3%"
            }
        })

    result = {
        "date": date,
        "flow_coverage": flow_cov,
        "top10": output
    }

    with open(RESULT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print("[ENGINE DONE]")

if __name__ == "__main__":
    run()
