"""
engine.py — v21.1 FINAL STABLE SSOT ENGINE

FIXED:
- date type inconsistency (str/int crash FIX)
- yfinance ticker failure handling
- IC computation robustness
- mobile + GitHub Actions stability

CORE:
- Cross-sectional alpha engine
- Momentum + volume flow proxy
- Self-learning history.csv
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
import yfinance as yf

warnings.filterwarnings("ignore")

# =========================
# PATHS
# =========================

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH  = os.path.join(ROOT, "result.json")

ENGINE_VERSION = "v21.1_FINAL_STABLE"

# =========================
# UNIVERSE
# =========================

UNIVERSE = [
    "005930.KS", "000660.KS", "035420.KS", "035720.KS",
    "051910.KS", "005380.KS", "006400.KS", "035500.KS",
    "000270.KS", "105560.KS", "055550.KS", "003550.KS",
    "012330.KS", "096770.KS", "034730.KS", "028260.KS",
    "017670.KS", "032830.KS", "086790.KS"
]

# =========================
# DATA LOADER (SAFE)
# =========================

def load_price_data():
    data = []

    for t in UNIVERSE:
        try:
            df = yf.Ticker(t).history(period="5d")

            if df is None or df.empty or "Close" not in df:
                continue

            data.append({
                "code": t.replace(".KS", ""),
                "name": t,
                "close": float(df["Close"].iloc[-1]),
                "volume": float(df["Volume"].iloc[-1]) if "Volume" in df else 0
            })

        except:
            continue

    return pd.DataFrame(data)

# =========================
# MOMENTUM
# =========================

def compute_momentum(df, hist):
    if hist is None or len(hist) < 30:
        df["mom"] = 0.0
        return df

    h = hist.copy()

    h["ret_5"] = h.groupby("code")["close"].pct_change(5)
    h["ret_20"] = h.groupby("code")["close"].pct_change(20)

    mom = (h["ret_5"] - h["ret_20"]).groupby(h["code"]).last()

    df["mom"] = df["code"].map(mom).fillna(0)

    return df

# =========================
# NORMALIZATION
# =========================

def zscore(x):
    x = np.array(x)
    std = np.std(x)
    if std == 0:
        return np.zeros(len(x))
    return (x - np.mean(x)) / std

def tanh(x):
    return np.tanh(x)

# =========================
# SCORING ENGINE
# =========================

def compute_scores(df):
    df = df.copy()

    df["mom"] = df["mom"].fillna(0)

    df["mom_z"] = zscore(df["mom"])
    df["flow_z"] = zscore(df["volume"])

    df["mom_sig"] = tanh(df["mom_z"])
    df["flow_sig"] = tanh(df["flow_z"])

    # stable weights
    fw = 0.4
    mw = 0.6

    df["score"] = fw * df["flow_sig"] + mw * df["mom_sig"]

    return df

# =========================
# HISTORY (FIXED TYPE SAFETY)
# =========================

def update_history(df, date):
    new = df[["code", "close", "score"]].copy()

    # 🔥 CRITICAL FIX: force string type
    new["date"] = str(date)

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)

        # 🔥 CRITICAL FIX: unify type
        hist["date"] = hist["date"].astype(str)

        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv(HISTORY_PATH, index=False)
    return hist

# =========================
# IC (ROBUST FIXED)
# =========================

def compute_ic(hist):
    if hist is None or len(hist) < 2:
        return None

    hist["date"] = hist["date"].astype(str)

    hist = hist.sort_values(["code", "date"])
    dates = sorted(hist["date"].unique())

    ics = []

    for i in range(1, len(dates)):
        t0, t1 = dates[i-1], dates[i]

        h0 = hist[hist["date"] == t0].set_index("code")
        h1 = hist[hist["date"] == t1].set_index("code")

        common = h0.index.intersection(h1.index)

        if len(common) < 10:
            continue

        ret = (h1.loc[common, "close"] / h0.loc[common, "close"] - 1)
        score = h0.loc[common, "score"]

        ic = score.corr(ret)

        if ic is not None and not np.isnan(ic):
            ics.append(ic)

    if len(ics) == 0:
        return None

    return float(np.mean(ics))

# =========================
# MAIN ENGINE
# =========================

def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    date = datetime.now().strftime("%Y%m%d")

    df = load_price_data()

    if df.empty:
        print("[ERROR] No data loaded")
        return

    hist = pd.read_csv(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None

    df = compute_momentum(df, hist)

    df = compute_scores(df)

    hist = update_history(df, date)

    ic = compute_ic(hist)

    top10 = df.nlargest(10, "score")

    result = {
        "version": ENGINE_VERSION,
        "date": date,
        "ic": ic,
        "top10": top10[["code", "close", "score"]].to_dict("records")
    }

    with open(RESULT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print("[TOP1]", top10.iloc[0]["code"] if len(top10) else None)
    print("[IC]", ic)
    print("[DONE]")


if __name__ == "__main__":
    run_engine()
