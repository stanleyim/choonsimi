"""
engine.py — v21 FINAL MOBILE-STABLE SSOT ENGINE

CORE DESIGN:
- Mobile-first (GitHub Actions 안정 실행)
- yfinance 기반 market data (no login / no KRX dependency)
- Cold start safe
- Cross-sectional IC enabled (20~50 universe)
- Self-learning history.csv
- Stable alpha signal generation

GOAL:
Turn mobile environment into working quant research system
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

ENGINE_VERSION = "v21.0_MOBILE_STABLE"

# =========================
# UNIVERSE (MOBILE OPTIMIZED)
# =========================

UNIVERSE = [
    "005930.KS",  # 삼성전자
    "000660.KS",  # SK하이닉스
    "035420.KS",  # NAVER
    "035720.KS",  # 카카오
    "051910.KS",  # LG화학
    "005380.KS",  # 현대차
    "006400.KS",  # 삼성SDI
    "035500.KS",  # LG전자
    "000270.KS",  # 기아
    "105560.KS",  # KB금융
    "055550.KS",  # 신한지주
    "003550.KS",  # LG
    "012330.KS",  # 현대모비스
    "096770.KS",  # SK이노베이션
    "034730.KS",  # SK
    "066570.KS",  # LG전자(우)
    "028260.KS",  # 삼성물산
    "017670.KS",  # SK텔레콤
    "032830.KS",  # 삼성생명
    "086790.KS"   # 하나금융
]

# =========================
# DATA LOADER
# =========================

def load_price_data():
    data = []

    for t in UNIVERSE:
        try:
            df = yf.Ticker(t).history(period="5d")
            if df.empty:
                continue

            close = df["Close"].iloc[-1]
            volume = df["Volume"].iloc[-1]

            data.append({
                "code": t.replace(".KS", ""),
                "name": t,
                "close": float(close),
                "volume": float(volume)
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
    df["mom_sig"] = tanh(df["mom_z"])

    # simple flow proxy (volume shock)
    df["flow"] = zscore(df["volume"])
    df["flow_sig"] = tanh(df["flow"])

    # weights (stable)
    fw = 0.4
    mw = 0.6

    df["score"] = fw * df["flow_sig"] + mw * df["mom_sig"]

    return df

# =========================
# HISTORY
# =========================

def update_history(df, date):
    new = df[["code", "close", "score"]].copy()
    new["date"] = date

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)
        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv(HISTORY_PATH, index=False)
    return hist

# =========================
# IC (CROSS-SECTIONAL)
# =========================

def compute_ic(hist):
    if hist is None or len(hist) < 2:
        return None

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

        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        return None

    return float(pd.Series(ics).mean())

# =========================
# MAIN ENGINE
# =========================

def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    date = datetime.now().strftime("%Y%m%d")

    df = load_price_data()

    if df.empty:
        print("[ERROR] No market data")
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
