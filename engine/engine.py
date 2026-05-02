"""
engine.py — v24.4 FINAL (dart_score fix + TOP10 + IC)
fetch_data.py → data.json → engine.py → result.json + history.csv + ic_log.json
"""

import os
import json
import warnings
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "data.json")
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH = os.path.join(ROOT, "result.json")
IC_LOG_PATH = os.path.join(ROOT, "ic_log.json")

ENGINE_VERSION = "v24.4_FINAL"
TOP_N = 10
MIN_IC_SAMPLE = 30
IC_WINDOW = 5
MOM_WINDOW = 20
FUTURE_WINDOW = 5
RETURN_20D_WINDOW = 20
EPS = 1e-9

# =========================
# UTIL
# =========================
def zscore(series):
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    std = s.std(ddof=1)
    return s if std == 0 or np.isnan(std) else (s - s.mean()) / std

def winsorize(s, p=0.01):
    s = pd.to_numeric(s, errors="coerce").fillna(0.0)
    return s.clip(s.quantile(p), s.quantile(1 - p))

def safe_corr(x, y):
    idx = x.dropna().index.intersection(y.dropna().index)
    x, y = x.loc[idx], y.loc[idx]
    if len(x) < MIN_IC_SAMPLE or x.std(ddof=1) == 0 or y.std(ddof=1) == 0:
        return None
    return float(x.corr(y))

# =========================
# 1. DATA LOAD
# =========================
def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    df = pd.DataFrame(raw["all"]).replace([np.inf, -np.inf], np.nan)
    df["code"] = df["code"].astype(str).str.zfill(6)
    print(f"[DATA] {len(df)}개 종목 로드 / 기준일: {raw.get('date', '?')}")
    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))

# =========================
# 2. DART
# =========================
def fetch_dart_score(date_str):
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        return pd.DataFrame(columns=["code", "dart_score"])

    try:
        end = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = (end - timedelta(days=7)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")
        url = "https://opendart.fss.or.kr/api/list.json"
        params = {"crtfc_key": dart_key, "bgn_de": start, "end_de": end_s, "page_count": 100}
        data = requests.get(url, params=params, timeout=10).json()

        if data.get("status")!= "000" or not data.get("list"):
            return pd.DataFrame(columns=["code", "dart_score"])

        dart_df = pd.DataFrame(data["list"])
        positive = ["배당", "자사주", "실적", "수주", "계약", "증가", "흑자"]
        negative = ["소송", "적자", "손실", "감소", "취소", "지연"]
        dart_df["dart_score"] = dart_df["report_nm"].apply(
            lambda t: sum(1 for k in positive if k in str(t)) - sum(1 for k in negative if k in str(t))
        )
        return dart_df.rename(columns={"stock_code": "code"}).groupby("code")["dart_score"].sum().reset_index()

    except Exception:
        return pd.DataFrame(columns=["code", "dart_score"])

# =========================
# 3. HIST FEATURES
# =========================
def compute_hist_features(hist_df):
    if hist_df is None or len(hist_df) < 30:
        return pd.DataFrame(columns=["code", "mom", "return_5d", "return_20d"])

    h = hist_df.sort_values(["code", "date"]).copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["ret"] = h.groupby("code")["close"].pct_change()

    h["mom"] = h.groupby("code")["ret"].rolling(MOM_WINDOW, min_periods=1).mean().reset_index(level=0, drop=True)
    h["return_5d"] = h.groupby("code")["close"].shift(-FUTURE_WINDOW) / h["close"] - 1
    h["return_20d"] = h.groupby("code")["close"].shift(-RETURN_20D_WINDOW) / h["close"] - 1

    return h.groupby("code")[["mom", "return_5d", "return_20d"]].last().reset_index()

# =========================
# 4. SCORE - DART FIX
# =========================
def compute_score(df):
    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"] = df["inst_net"].fillna(0)
    df["mom"] = df.get("mom", 0).fillna(0)
    df["volume"] = df["volume"].fillna(1).replace(0, 1)
    df["close"] = df["close"].fillna(1).replace(0, 1)

    # DART FIX: Series인지 float인지 체크
    dart_col = df.get("dart_score")
    df["dart_score"] = dart_col.fillna(0.0) if isinstance(dart_col, pd.Series) else pd.Series(0.0, index=df.index)

    df["flow"] = df["foreign_net"] + df["inst_net"]
    df["turnover"] = df["close"] * df["volume"]
    df["flow_ratio"] = winsorize(df["flow"] / (df["turnover"] + EPS))
    df["flow_neutral"] = df["flow_ratio"] - df["flow_ratio"].mean()

    df["flow_z"] = zscore(df["flow_neutral"])
    df["mom_z"] = zscore(df["mom"])
    df["dart_z"] = zscore(df["dart_score"])
    df["score"] = 0.5 * df["flow_z"] + 0.3 * df["mom_z"] + 0.2 * df["dart_z"]
    return df

# =========================
# 5. HISTORY - TOP10 ONLY
# =========================
def update_history(df, date_str):
    today = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    df_top = df.sort_values("score", ascending=False).head(TOP_N).copy()
    df_top["date"] = today

    cols = ["date", "code", "close", "score", "mom_z", "flow_z", "dart_z", "return_5d", "return_20d"]
    header = not os.path.exists(HISTORY_PATH)
    df_top.to_csv(HISTORY_PATH, mode="a", header=header, index=False, columns=cols)
    return df_top

# =========================
# 6. ROLLING IC
# =========================
def compute_ic_series(hist, window=IC_WINDOW):
    if hist is None or len(hist) < MIN_IC_SAMPLE:
        return None

    dates = sorted(hist["date"].unique())
    ic_list = []
    for d in dates:
        day_data = hist[hist["date"] == d][["score", "return_5d"]].dropna()
        if len(day_data) >= MIN_IC_SAMPLE:
            ic = safe_corr(day_data["score"], day_data["return_5d"])
            if ic is not None:
                ic_list.append(ic)
    return float(pd.Series(ic_list).tail(window).mean()) if ic_list else None

# =========================
# MAIN ENGINE
# =========================
def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    df, date_str = load_data()
    if df.empty:
        return

    dart_df = fetch_dart_score(date_str)
    df = df.merge(dart_df, on="code", how="left") if len(dart_df) > 0 else df.assign(dart_score=0.0)

    hist_prev = pd.read_csv(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None
    hist_feat = compute_hist_features(hist_prev)
    df = df.merge(hist_feat, on="code", how="left")

    df = compute_score(df)
    update_history(df, date_str)

    ic = None
    if os.path.exists(HISTORY_PATH):
        hist_full = pd.read_csv(HISTORY_PATH)
        hist_60d = hist_full[hist_full["date"] >= (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")]
        if len(hist_60d) > 100:
            ic = compute_ic_series(hist_60d)
            if ic is not None:
                ic_log = {
                    "date": date_str,
                    "ic_score": round(ic, 4),
                    "ic_mom": round(safe_corr(hist_60d["mom_z"], hist_60d["return_5d"]) or 0, 4),
                    "ic_flow": round(safe_corr(hist_60d["flow_z"], hist_60d["return_5d"]) or 0, 4),
                    "ic_dart": round(safe_corr(hist_60d["dart_z"], hist_60d["return_5d"]) or 0, 4)
                }
                with open(IC_LOG_PATH, "w", encoding="utf-8") as f:
                    json.dump(ic_log, f, indent=2)

    df["score_norm"] = 50.0 if df["score"].max() - df["score"].min() < EPS else \
                       ((df["score"] - df["score"].min()) / (df["score"].max() - df["score"].min()) * 100).round(2)

    df_top = df.sort_values("score", ascending=False).head(TOP_N).reset_index(drop=True)
    cols = ["code", "name", "close", "score_norm", "flow_z", "mom_z", "dart_z",
            "foreign_net", "inst_net", "dart_score", "volume", "return_5d", "return_20d"]
    records = df_top.rename(columns={"score_norm": "score"}).to_dict("records")

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "version": ENGINE_VERSION,
            "biz_day": date_str,
            "ic": None if ic is None else round(ic, 4),
            "ic_window": IC_WINDOW,
            "count": len(df),
            "top10": records
        }, f, ensure_ascii=False, indent=2)

    print(f"[ENGINE DONE] TOP1: {records[0]['name']} / score {records[0]['score']}")

if __name__ == "__main__":
    run_engine()
