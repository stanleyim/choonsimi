import pandas as pd
import numpy as np
import os

from flow import build_flow_data
from ic_manager import update_ic, compute_weights
from portfolio import build_portfolio, load_prev_portfolio, save_portfolio, compare_portfolio

# =========================
# CONFIG
# =========================
FLOW_WINDOWS = [3, 5, 10]
FLOW_WEIGHTS = [0.5, 0.3, 0.2]

VOL_WINDOW = 5
TOP_N = 10
MIN_VALID_N = 30
EPS = 1e-6

TURNOVER_THRESHOLD = 50e8

# ✅ ROOT 기준 (중요)
HISTORY_FILE = "../history.csv"


# =========================
# UTIL
# =========================
def zscore(s):
    m = s.mean()
    sd = s.std()
    if sd == 0 or np.isnan(sd):
        return pd.Series([0]*len(s), index=s.index)
    return (s - m) / sd


# =========================
# HISTORY
# =========================
def update_history(df):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    new_data = df[["code", "close"]].copy()
    new_data["date"] = today

    if os.path.exists(HISTORY_FILE):
        hist = pd.read_csv(HISTORY_FILE)
        hist = pd.concat([hist, new_data])
        hist = hist.drop_duplicates(subset=["code", "date"])
    else:
        hist = new_data

    hist.to_csv(HISTORY_FILE, index=False)
    return hist


# =========================
# FLOW
# =========================
def compute_flow(df):
    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"] = df["inst_net"].fillna(0)

    df["flow_raw"] = df["foreign_net"] + df["inst_net"]

    signals = []
    for w, weight in zip(FLOW_WINDOWS, FLOW_WEIGHTS):
        ma = df["flow_raw"].rolling(w, min_periods=1).mean()
        delta = df["flow_raw"] - ma
        signals.append(zscore(delta) * weight)

    df["flow_z"] = sum(signals)
    return df


# =========================
# MOMENTUM (v11.4 FIXED)
# =========================
def compute_momentum(df, hist):

    if hist is None or len(hist) < 50:
        df["mom_z"] = 0
        return df

    hist = hist.sort_values(["code", "date"])

    hist["ret_1d"] = hist.groupby("code")["close"].pct_change()
    hist["ret_5d"] = hist.groupby("code")["close"].pct_change(5)
    hist["ret_10d"] = hist.groupby("code")["close"].pct_change(10)

    latest = hist.groupby("code").tail(1)

    mom_map = {}
    for _, r in latest.iterrows():
        mom_map[r["code"]] = (
            0.5 * (r.get("ret_1d") or 0) +
            0.3 * (r.get("ret_5d") or 0) +
            0.2 * (r.get("ret_10d") or 0)
        )

    df["mom_raw"] = df["code"].map(mom_map).fillna(0)
    df["mom_z"] = zscore(df["mom_raw"])

    return df


# =========================
# DART
# =========================
def compute_dart(df):
    df["dart_score"] = df["dart_score"].fillna(0)
    df["dart_ma3"] = df["dart_score"].rolling(3, min_periods=1).mean()
    df["dart_delta"] = df["dart_score"] - df["dart_ma3"]
    df["dart_z"] = zscore(df["dart_delta"])
    return df


# =========================
# NEXT RETURN
# =========================
def compute_next_return(df):
    df["next_return"] = df["close"].shift(-1) / df["close"] - 1
    return df


# =========================
# IC
# =========================
def compute_ic(df):
    valid = df.dropna(subset=["flow_z", "mom_z", "dart_z", "next_return"])

    if len(valid) < MIN_VALID_N:
        return None

    return (
        valid["flow_z"].corr(valid["next_return"]),
        valid["mom_z"].corr(valid["next_return"]),
        valid["dart_z"].corr(valid["next_return"])
    )


# =========================
# SCORE
# =========================
def compute_score(df, w_flow, w_mom, w_dart):
    df["score"] = (
        w_flow * df["flow_z"] +
        w_mom * df["mom_z"] +
        w_dart * df["dart_z"]
    )
    return df


# =========================
# FILTER
# =========================
def apply_score_filter(df):
    thr = df["score"].quantile(0.6)
    df = df[df["score"] > thr]

    if len(df) < TOP_N * 3:
        df = df.sort_values("score", ascending=False).head(TOP_N * 3)

    return df


# =========================
# VOL (FIXED CROSS-SECTION BIAS)
# =========================
def apply_vol_weight(df):

    vol = df.groupby("code")["close"].pct_change().rolling(VOL_WINDOW).std()
    inv_vol = 1 / (vol + EPS)

    df["adj_score"] = df["score"] * inv_vol

    total = df["adj_score"].sum()

    if total == 0 or np.isnan(total):
        df["weight"] = 1 / len(df)
    else:
        df["weight"] = df["adj_score"] / total

    return df


# =========================
# MAIN
# =========================
def run_engine(df):

    print("[ENGINE v11.4 START]")

    df = df[df["close"].notna()]
    df = df[df["volume"] > 0]

    df["turnover"] = df["close"] * df["volume"]
    df = df[df["turnover"] > TURNOVER_THRESHOLD]

    if len(df) < 50:
        df = df.sort_values("turnover", ascending=False).head(200)

    codes = df["code"].astype(str).str.zfill(6).tolist()
    flow_map = build_flow_data(codes)

    df["foreign_net"] = df["code"].map(lambda x: flow_map.get(str(x).zfill(6), {}).get("foreign_net", 0))
    df["inst_net"] = df["code"].map(lambda x: flow_map.get(str(x).zfill(6), {}).get("inst_net", 0))

    hist = update_history(df)

    df = compute_momentum(df, hist)
    df = compute_flow(df)
    df = compute_dart(df)
    df = compute_next_return(df)

    ic_vals = compute_ic(df)

    if ic_vals is None:
        w_flow, w_mom, w_dart = 0.6, 0.0, 0.4
    else:
        flow_ic, mom_ic, dart_ic = ic_vals
        update_ic(flow_ic, mom_ic, dart_ic)
        w_flow, w_mom, w_dart = compute_weights()

    df = compute_score(df, w_flow, w_mom, w_dart)
    df = apply_score_filter(df)
    df = apply_vol_weight(df)

    new_port = build_portfolio(df, top_n=TOP_N)
    prev_port = load_prev_portfolio()

    added, removed = compare_portfolio(prev_port, new_port)

    save_portfolio(new_port)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    df = pd.read_json("data.json")
    run_engine(df)
