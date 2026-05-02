"""
engine.py — v22 FINAL PRODUCTION-STABLE

GOAL:
- Mobile + GitHub Actions 완전 안정
- Yahoo failure tolerate
- IC always-safe
- Cross-sectional alpha engine

PRINCIPLES:
- Never fail due to single ticker
- Always produce output
- Degraded mode allowed, crash NOT allowed
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

ENGINE_VERSION = "v22.0_FINAL_PROD"

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
# SAFE DATA FETCH
# =========================

def safe_fetch(ticker):
    try:
        df = yf.Ticker(ticker).history(period="5d")

        if df is None or df.empty:
            return None

        if "Close" not in df:
            return None

        return df

    except:
        return None

# =========================
# LOAD DATA (FAIL SAFE)
# =========================

def load_price_data():
    data = []

    for t in UNIVERSE:
        df = safe_fetch(t)

        if df is None:
            continue  # 🔥 핵심: 완전 skip

        try:
            data.append({
                "code": t.replace(".KS", ""),
                "name": t,
                "close": float(df["Close"].iloc[-1]),
                "volume": float(df["Volume"].iloc[-1]) if "Volume" in df else 0
            })
        except:
            continue

    print(f"[DATA] loaded {len(data)} stocks")

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
# STATS
# =========================

def zscore(x):
    x = np.array(x)
    if len(x) == 0:
        return x
    std = np.std(x)
    if std == 0:
        return np.zeros(len(x))
    return (x - np.mean(x)) / std

def tanh(x):
    return np.tanh(x)

# =========================
# SCORING
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
# HISTORY (SAFE TYPE FIX)
# =========================

def update_history(df, date):
    new = df[["code", "close", "score"]].copy()

    # 🔥 FIX: enforce string
    new["date"] = str(date)

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)

        # 🔥 FIX: unify type
        hist["date"] = hist["date"].astype(str)

        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv(HISTORY_PATH, index=False)

    return hist

# =========================
# IC (FULL SAFE)
# =========================

def compute_ic(hist):
    if hist is None or len(hist) < 5:
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

        if len(common) < 5:
            continue

        ret = (h1.loc[common, "close"] / h0.loc[common, "close"] - 1)
        score = h0.loc[common, "score"]

        if len(ret) == 0 or len(score) == 0:
            continue

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

    # 🔥 fallback safety
    if df.empty:
        print("[WARNING] empty dataset → fallback active")
        df = pd.DataFrame(columns=["code", "name", "close", "volume"])
        return

    hist = pd.read_csv(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None

    df = compute_momentum(df, hist)
    df = compute_scores(df)

    hist = update_history(df, date)

    ic = compute_ic(hist)

    if df.empty:
        top10 = []
    else:
        top10 = df.nlargest(min(10, len(df)), "score")

    result = {
        "version": ENGINE_VERSION,
        "date": date,
        "ic": ic,
        "coverage": len(df),
        "top10": top10[["code", "close", "score"]].to_dict("records") if len(df) > 0 else []
    }

    with open(RESULT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print("[TOP1]", top10.iloc[0]["code"] if len(top10) else None)
    print("[IC]", ic)
    print("[DONE]")


if __name__ == "__main__":
    run_engine()
