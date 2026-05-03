"""
engine/engine.py — v25 STABLE FINAL
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

# =========================
# PATH (핵심 수정)
# =========================
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_PATH = os.path.join(ROOT, "data.json")
RESULT_PATH = os.path.join(ROOT, "result.json")
HISTORY_PATH = os.path.join(ROOT, "history.csv")

TOP_N = 10
EPS = 1e-9


# =========================
# UTIL
# =========================
def zscore(s):
    s = pd.to_numeric(s, errors="coerce").fillna(0)
    std = s.std(ddof=1)
    if std == 0 or np.isnan(std):
        return pd.Series(0, index=s.index)
    return (s - s.mean()) / std


# =========================
# LOAD
# =========================
def load():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"data.json 없음: {DATA_PATH}")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])

    df["code"] = df["code"].astype(str).str.zfill(6)

    # 안전 처리
    df["close"] = pd.to_numeric(df.get("close", 0), errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

    df["foreign_net"] = pd.to_numeric(df.get("foreign_net", 0), errors="coerce").fillna(0)
    df["inst_net"] = pd.to_numeric(df.get("inst_net", 0), errors="coerce").fillna(0)

    df["dart_score"] = pd.to_numeric(df.get("dart_score", 0), errors="coerce").fillna(0)

    print(f"[LOAD] {len(df)} 종목")

    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))


# =========================
# SCORE
# =========================
def compute_score(df):
    has_flow = (df["foreign_net"].abs() > 0).any() or (df["inst_net"].abs() > 0).any()

    if has_flow:
        w_flow, w_mom, w_dart = 0.5, 0.3, 0.2
        print("[SCORE] flow=50%")
    else:
        w_flow, w_mom, w_dart = 0.0, 0.5, 0.5
        print("[SCORE] flow 없음 → mom/dart")

    df["flow"] = df["foreign_net"] + df["inst_net"]
    df["turnover"] = df["close"] * df["volume"]

    df["flow_ratio"] = df["flow"] / (df["turnover"] + EPS)

    df["flow_z"] = zscore(df["flow_ratio"])
    df["mom_z"] = zscore(df["close"].pct_change().fillna(0))
    df["dart_z"] = zscore(df["dart_score"])

    df["score"] = w_flow * df["flow_z"] + w_mom * df["mom_z"] + w_dart * df["dart_z"]

    return df


# =========================
# HISTORY
# =========================
def update_history(df, date):

    df = df.copy()
    df["date"] = date

    cols = ["date", "code", "close", "score"]
    df = df[cols]

    if os.path.exists(HISTORY_PATH):
        old = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        df = pd.concat([old, df])

    df = df.drop_duplicates(["date", "code"])
    df.to_csv(HISTORY_PATH, index=False)

    print(f"[HISTORY] {len(df)} rows")


# =========================
# MAIN
# =========================
def run():
    print("[ENGINE START]")

    df, date = load()

    df = compute_score(df)

    # 정규화
    smin, smax = df["score"].min(), df["score"].max()
    if smax - smin < EPS:
        df["score_norm"] = 50
    else:
        df["score_norm"] = (df["score"] - smin) / (smax - smin) * 100

    top = df.sort_values("score_norm", ascending=False).head(TOP_N)

    records = top[["code", "close", "score_norm"]].rename(
        columns={"score_norm": "score"}
    ).to_dict("records")

    result = {
        "date": date,
        "count": len(df),
        "top10": records,
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    update_history(top, date)

    print("[ENGINE DONE]")


if __name__ == "__main__":
    run()
