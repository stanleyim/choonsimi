import pandas as pd
import numpy as np
import json
import os
import warnings

warnings.filterwarnings("ignore", category=RuntimeWarning)

from flow import build_flow_data
from ic_manager import update_ic, compute_weights
from portfolio import build_portfolio, load_prev_portfolio, save_portfolio, compare_portfolio


# =========================
# CONFIG
# =========================
FLOW_WINDOWS = [3, 5, 10]
FLOW_WEIGHTS  = [0.5, 0.3, 0.2]

VOL_WINDOW         = 5
TOP_N              = 10
MIN_VALID_N        = 30
EPS                = 1e-6
TURNOVER_THRESHOLD = 50e8

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE    = os.path.join(ROOT, "data.json")
HISTORY_FILE = os.path.join(ROOT, "history.csv")
RESULT_FILE  = os.path.join(ROOT, "result.json")


# =========================
# UTIL
# =========================
def zscore(s: pd.Series) -> pd.Series:
    sd = s.std()
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / (sd + EPS)


# =========================
# HISTORY
# =========================
def update_history(df: pd.DataFrame) -> pd.DataFrame:
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    new_rows = df[["code", "close"]].copy()
    new_rows["date"] = today

    if os.path.exists(HISTORY_FILE):
        hist = pd.read_csv(HISTORY_FILE, dtype={"code": str})
        hist = pd.concat([hist, new_rows], ignore_index=True)
        hist = hist.drop_duplicates(subset=["code", "date"], keep="last")
    else:
        hist = new_rows

    hist.to_csv(HISTORY_FILE, index=False)
    return hist


# =========================
# FLOW
# =========================
def compute_flow(df: pd.DataFrame) -> pd.DataFrame:
    df["foreign_net"] = df.get("foreign_net", 0).fillna(0)
    df["inst_net"]    = df.get("inst_net", 0).fillna(0)

    df["flow_raw"] = df["foreign_net"] + df["inst_net"]

    signals = []
    for w, wt in zip(FLOW_WINDOWS, FLOW_WEIGHTS):
        ma = df.groupby("code")["flow_raw"].transform(
            lambda x: x.rolling(w, min_periods=1).mean()
        )
        delta = df["flow_raw"] - ma
        signals.append(zscore(delta) * wt)

    df["flow_z"] = sum(signals)
    return df


# =========================
# MOMENTUM
# =========================
def compute_momentum(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    if hist is None or len(hist) < 50:
        df["mom_z"] = 0.0
        return df

    h = hist.sort_values(["code", "date"]).copy()

    h["ret_1"]  = h.groupby("code")["close"].pct_change(1)
    h["ret_5"]  = h.groupby("code")["close"].pct_change(5)
    h["ret_10"] = h.groupby("code")["close"].pct_change(10)

    latest = h.groupby("code").tail(1)

    mom_map = {}
    for _, r in latest.iterrows():
        mom_map[r["code"]] = (
            0.5 * np.nan_to_num(r["ret_1"], 0.0) +
            0.3 * np.nan_to_num(r["ret_5"], 0.0) +
            0.2 * np.nan_to_num(r["ret_10"], 0.0)
        )

    df["mom_raw"] = df["code"].map(mom_map).fillna(0.0)
    df["mom_z"]   = zscore(df["mom_raw"])
    return df


# =========================
# DART
# =========================
def compute_dart(df: pd.DataFrame) -> pd.DataFrame:
    df["dart_score"] = df.get("dart_score", 0).fillna(0)

    df["dart_ma3"] = df.groupby("code")["dart_score"].transform(
        lambda x: x.rolling(3, min_periods=1).mean()
    )

    df["dart_delta"] = df["dart_score"] - df["dart_ma3"]
    df["dart_z"] = zscore(df["dart_delta"])
    return df


# =========================
# NEXT RETURN (IC 안정형)
# =========================
def compute_next_return(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    if hist is None or len(hist) < 10:
        df["next_return"] = np.nan
        return df

    h = hist.sort_values(["code", "date"]).copy()

    h["ret_1d"] = h.groupby("code")["close"].pct_change()
    h["next_return"] = h.groupby("code")["ret_1d"].shift(-1)

    nr_map = h.groupby("code")["next_return"].last()
    df["next_return"] = df["code"].map(nr_map)

    return df


# =========================
# IC
# =========================
def compute_ic(df: pd.DataFrame):
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
def compute_score(df: pd.DataFrame, w: dict) -> pd.DataFrame:
    df["score"] = (
        w.get("flow_z", 0) * df["flow_z"] +
        w.get("mom_z", 0)  * df["mom_z"] +
        w.get("dart_z", 0) * df["dart_z"]
    )
    return df


# =========================
# FILTER
# =========================
def apply_filter(df: pd.DataFrame) -> pd.DataFrame:
    thr = df["score"].quantile(0.6)
    filtered = df[df["score"] > thr]

    if len(filtered) < TOP_N * 3:
        return df.sort_values("score", ascending=False).head(TOP_N * 3)

    return filtered


# =========================
# VOL
# =========================
def apply_vol(df: pd.DataFrame) -> pd.DataFrame:
    ret = df.groupby("code")["close"].transform(lambda x: x.pct_change())

    vol = ret.groupby(df["code"]).transform(
        lambda x: x.rolling(VOL_WINDOW, min_periods=1).std()
    ).fillna(EPS)

    df["adj_score"] = df["score"] / (vol + EPS)

    total = df["adj_score"].sum()
    df["weight"] = (
        df["adj_score"] / total
        if total != 0 else 1.0 / len(df)
    )

    return df


# =========================
# SAVE
# =========================
def save_result(df: pd.DataFrame, add=None, rem=None):
    cols = ["code", "close", "score", "weight", "flow_z", "mom_z", "dart_z"]

    top = df.sort_values("score", ascending=False).head(TOP_N)[cols].copy()

    smin, smax = top["score"].min(), top["score"].max()
    if smax != smin:
        top["score"] = ((top["score"] - smin) / (smax - smin) * 100).round(2)
    else:
        top["score"] = 50.0

    result = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "top10": top.to_dict("records"),
        "changes": {
            "add": list(add or []),
            "remove": list(rem or [])
        }
    }

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# =========================
# ENGINE
# =========================
def run_engine(df: pd.DataFrame):
    print("[ENGINE START]")

    df = df[df["close"].notna()].copy()
    df = df[df["volume"] > 0]

    df["code"] = df["code"].astype(str).str.zfill(6)
    df["turnover"] = df["close"] * df["volume"]
    df = df[df["turnover"] > TURNOVER_THRESHOLD]

    if len(df) < 50:
        df = df.sort_values("turnover", ascending=False).head(200)

    df = df.reset_index(drop=True)

    flow_map = build_flow_data(df["code"].tolist())

    df["foreign_net"] = df["code"].map(lambda x: flow_map.get(x, {}).get("foreign_net", 0))
    df["inst_net"]    = df["code"].map(lambda x: flow_map.get(x, {}).get("inst_net", 0))

    hist = update_history(df)

    df = compute_flow(df)
    df = compute_momentum(df, hist)
    df = compute_dart(df)
    df = compute_next_return(df, hist)

    ic = compute_ic(df)

    if ic is None or any(np.isnan(x) for x in ic):
        w = {"flow_z": 0.5, "mom_z": 0.2, "dart_z": 0.3}
    else:
        update_ic(*ic)
        w = compute_weights()

    df = compute_score(df, w)
    df = apply_filter(df)
    df = apply_vol(df)

    new_port = build_portfolio(df, TOP_N)
    prev_port = load_prev_portfolio()

    add, rem = compare_portfolio(prev_port, new_port)
    save_portfolio(new_port)

    save_result(df, add, rem)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    run_engine(df)
