"""
engine.py — FINAL STABLE VERSION (IC FIXED)
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


# =========================
# SAFE UTILS
# =========================
def safe_clean(df):
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.fillna(0.0)
    return df


def safe_corr(x, y):
    x = x.fillna(0)
    y = y.fillna(0)

    if len(x) < MIN_IC_SAMPLE:
        return None
    if x.std() == 0 or y.std() == 0:
        return None

    return float(x.corr(y))


# =========================
# HISTORY
# =========================
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


# =========================
# FEATURES
# =========================
def compute_flow(df):
    df["flow"] = df["foreign_net"] + df["inst_net"]
    return df


def compute_momentum(df, hist):
    if hist is None or len(hist) < 10:
        df["mom"] = 0.0
        return df

    h = hist.sort_values(["code", "date"])
    h["ret"] = h.groupby("code")["close"].pct_change()

    mom = h.groupby("code")["ret"].mean()
    df["mom"] = df["code"].map(mom).fillna(0.0)

    return df


def compute_score(df):
    df["score"] = (
        0.5 * df["flow"] +
        0.3 * df["mom"] +
        0.2 * df["dart_score"]
    )
    return df


# =========================
# IC (핵심)
# =========================
def compute_ic(df):
    valid = df.copy()

    # next return proxy (단순화 안정 버전)
    valid["next_return"] = valid["close"].pct_change().fillna(0)

    ic = safe_corr(valid["score"], valid["next_return"])

    return ic


# =========================
# ENGINE
# =========================
def run():
    print("[ENGINE START]")

    # load data
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df = safe_clean(df)

    # history
    hist = update_history(df)

    # factors
    df = compute_flow(df)
    df = compute_momentum(df, hist)
    df = compute_score(df)

    # IC
    ic = compute_ic(df)

    print("[IC]", ic)

    # TOP selection
    df = df.sort_values("score", ascending=False).head(TOP_N)

    # =========================
    # FINAL OUTPUT (IMPORTANT)
    # =========================
    result = {
        "top10": df.to_dict("records"),
        "ic": None if (ic is None or np.isnan(ic)) else float(ic),
        "count": int(len(df))
    }

    # JSON SAFE WRITE
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[DONE]")


if __name__ == "__main__":
    run()
