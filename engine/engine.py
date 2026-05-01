"""
engine.py — Quant Engine (FINAL IC-STABLE VERSION)
"""

import json
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_FILE = os.path.join(ROOT, "data.json")
HISTORY_FILE = os.path.join(ROOT, "history.csv")
RESULT_FILE = os.path.join(ROOT, "result.json")

TOP_N = 10
MIN_IC_SAMPLE = 30


# -------------------------
# SAFE UTILS
# -------------------------
def safe_corr(x, y):
    if len(x) < MIN_IC_SAMPLE:
        return None
    if x.std() == 0 or y.std() == 0:
        return None
    return x.corr(y)


def clean(df):
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.fillna(0.0)
    return df


# -------------------------
# HISTORY
# -------------------------
def update_history(df):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    new = df[["code", "close"]].copy()
    new["date"] = today

    if os.path.exists(HISTORY_FILE):
        hist = pd.read_csv(HISTORY_FILE)
        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv(HISTORY_FILE, index=False)
    return hist


# -------------------------
# FEATURES
# -------------------------
def compute_momentum(df, hist):
    if hist is None or len(hist) < 10:
        df["mom"] = 0
        return df

    h = hist.sort_values(["code", "date"])
    h["ret"] = h.groupby("code")["close"].pct_change()

    mom = h.groupby("code")["ret"].mean()
    df["mom"] = df["code"].map(mom).fillna(0)

    return df


def compute_flow(df):
    df["flow"] = df["foreign_net"] + df["inst_net"]
    return df


def compute_score(df):
    df["score"] = (
        0.5 * df["flow"] +
        0.3 * df["mom"] +
        0.2 * df["dart_score"]
    )
    return df


# -------------------------
# IC
# -------------------------
def compute_ic(df):
    valid = df.dropna(subset=["score"])

    if len(valid) < MIN_IC_SAMPLE:
        return None

    ic = safe_corr(valid["score"], valid["close"].pct_change().fillna(0))
    return ic


# -------------------------
# MAIN
# -------------------------
def run():
    print("[ENGINE START]")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])

    df = clean(df)

    hist = update_history(df)

    df = compute_flow(df)
    df = compute_momentum(df, hist)

    df = compute_score(df)

    ic = compute_ic(df)

    print("[IC]", ic)

    df = df.sort_values("score", ascending=False).head(TOP_N)

    result = {
        "top10": df.to_dict("records"),
        "ic": ic,
        "count": len(df)
    }

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run()
