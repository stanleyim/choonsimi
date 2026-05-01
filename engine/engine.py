import json
import os
import numpy as np
import pandas as pd

EPS = 1e-9

# =========================
# SAFE UTILS
# =========================
def safe_num(x):
    if x is None:
        return 0.0
    if isinstance(x, float) and np.isnan(x):
        return 0.0
    return float(x)


def zscore(s: pd.Series):
    s = s.astype(float)
    if s.std() == 0 or np.isnan(s.std()):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / (s.std() + EPS)


# =========================
# HISTORY
# =========================
def update_history(df):
    from datetime import datetime
    date = datetime.now().strftime("%Y-%m-%d")

    new = df[["code", "close"]].copy()
    new["date"] = date

    if os.path.exists("history.csv"):
        hist = pd.read_csv("history.csv", dtype={"code": str})
        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv("history.csv", index=False)
    return hist


# =========================
# FLOW (FIXED = 0)
# =========================
def compute_flow(df):
    df["foreign_net"] = 0.0
    df["inst_net"] = 0.0
    df["flow_z"] = 0.0
    return df


# =========================
# MOMENTUM (FIXED)
# =========================
def compute_momentum(df, hist):
    if hist is None or len(hist) < 20:
        df["mom_z"] = 0.0
        return df

    h = hist.sort_values(["code", "date"]).copy()

    h["ret_1"] = h.groupby("code")["close"].pct_change(1)
    h["ret_5"] = h.groupby("code")["close"].pct_change(5)
    h["ret_10"] = h.groupby("code")["close"].pct_change(10)

    last = h.groupby("code").tail(1).copy()

    last["mom_raw"] = (
        0.5 * last["ret_1"].fillna(0) +
        0.3 * last["ret_5"].fillna(0) +
        0.2 * last["ret_10"].fillna(0)
    )

    df["mom_raw"] = df["code"].map(last.set_index("code")["mom_raw"]).fillna(0.0)
    df["mom_z"] = zscore(df["mom_raw"])

    return df


# =========================
# DART (SAFE)
# =========================
def compute_dart(df):
    df["dart_score"] = df.get("dart_score", 0.0).fillna(0.0)
    df["dart_z"] = zscore(df["dart_score"])
    return df


# =========================
# NEXT RETURN
# =========================
def compute_next_return(df, hist):
    if hist is None or len(hist) < 20:
        df["next_return"] = np.nan
        return df

    h = hist.sort_values(["code", "date"]).copy()
    h["next_return"] = h.groupby("code")["close"].pct_change().shift(-1)

    last = h.groupby("code")["next_return"].last()

    df["next_return"] = df["code"].map(last)

    return df


# =========================
# IC
# =========================
def compute_ic(df):
    valid = df.dropna(subset=["mom_z", "next_return"])

    if len(valid) < 30:
        return None

    ic = valid["mom_z"].corr(valid["next_return"])

    if np.isnan(ic):
        return None

    return ic


# =========================
# SCORE
# =========================
def compute_score(df):
    df["score"] = df["mom_z"] + df["dart_z"]
    return df


# =========================
# FILTER
# =========================
def apply_filter(df):
    return df.sort_values("score", ascending=False).head(50)


# =========================
# SAVE
# =========================
def save_result(df):
    top = df.sort_values("score", ascending=False).head(10)

    result = {
        "top10": top.replace({np.nan: None}).to_dict("records")
    }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# =========================
# MAIN
# =========================
def run(df):
    df = df.copy()

    df = compute_flow(df)
    hist = update_history(df)

    df = compute_momentum(df, hist)
    df = compute_dart(df)
    df = compute_next_return(df, hist)

    ic = compute_ic(df)
    print("[IC]", ic)

    df = compute_score(df)
    df = apply_filter(df)

    save_result(df)


if __name__ == "__main__":
    with open("data.json") as f:
        data = json.load(f)

    df = pd.DataFrame(data["all"])
    run(df)
