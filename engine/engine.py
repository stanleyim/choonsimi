"""
engine.py — v24 FINAL (data.json 기반 / 완전 안정)
fetch_data.py → data.json → engine.py → result.json
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

ENGINE_VERSION = "v24.0_STABLE"

TOP_N = 10
MIN_IC_SAMPLE = 30
IC_WINDOW = 5
MOM_WINDOW = 5
EPS = 1e-9

# =========================
# UTIL
# =========================
def zscore(series):
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    std = s.std(ddof=1)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std

def winsorize(s, p=0.01):
    s = pd.to_numeric(s, errors="coerce").fillna(0.0)
    lower = s.quantile(p)
    upper = s.quantile(1 - p)
    return s.clip(lower, upper)

def safe_corr(x, y):
    idx = x.dropna().index.intersection(y.dropna().index)
    x, y = x.loc[idx], y.loc[idx]
    if len(x) < MIN_IC_SAMPLE:
        return None
    if x.std(ddof=1) == 0 or y.std(ddof=1) == 0:
        return None
    return float(x.corr(y))

# =========================
# 1. DATA LOAD
# =========================
def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df = df.replace([np.inf, -np.inf], np.nan)
    df["code"] = df["code"].astype(str).str.zfill(6) # 코드 6자리 통일

    print(f"[DATA] {len(df)}개 종목 로드 / 기준일: {raw.get('date', '?')}")
    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))

# =========================
# 2. DART
# =========================
def fetch_dart_score(date_str):
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        print("[DART] API KEY 없음 → skip")
        return pd.DataFrame(columns=["code", "dart_score"])

    try:
        end = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = (end - timedelta(days=7)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": dart_key,
            "bgn_de": start,
            "end_de": end_s,
            "page_count": 100,
        }

        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if data.get("status")!= "000":
            raise ValueError(f"DART status: {data.get('status')}")

        items = data.get("list", [])
        if not items:
            return pd.DataFrame(columns=["code", "dart_score"])

        dart_df = pd.DataFrame(items)
        positive = ["배당", "자사주", "실적", "수주", "계약", "증가", "흑자"]
        negative = ["소송", "적자", "손실", "감소", "취소", "지연"]

        def score_report(title):
            s = 0
            for k in positive:
                if k in str(title): s += 1
            for k in negative:
                if k in str(title): s -= 1
            return s

        dart_df["dart_score"] = dart_df["report_nm"].apply(score_report)
        dart_df = dart_df.rename(columns={"stock_code": "code"})
        dart_df["code"] = dart_df["code"].astype(str).str.zfill(6)

        result = dart_df.groupby("code")["dart_score"].sum().reset_index()
        print(f"[DART] {len(result)}개 종목 공시 수집")
        return result

    except Exception as e:
        print(f"[DART ERROR] → skip: {e}")
        return pd.DataFrame(columns=["code", "dart_score"])

# =========================
# 3. MOMENTUM - HISTORY SAFE
# =========================
def compute_momentum(df, hist):
    if hist is None or len(hist) < 10:
        df["mom"] = 0.0
        return df

    h = hist.sort_values(["code", "date"]).copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["ret"] = h.groupby("code")["close"].pct_change()

    h["mom"] = (
        h.groupby("code")["ret"]
       .rolling(MOM_WINDOW, min_periods=1)
       .mean()
       .reset_index(level=0, drop=True)
    )

    mom = h.groupby("code")["mom"].last()
    df["mom"] = df["code"].map(mom).fillna(0.0)
    return df

# =========================
# 4. SCORE - DART SAFE
# =========================
def compute_score(df):
    # SSOT: 모든 컬럼 0 패딩 보장
    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"] = df["inst_net"].fillna(0)
    df["mom"] = df["mom"].fillna(0)
    df["dart_score"] = df["dart_score"].fillna(0) # ← KeyError 방지
    df["volume"] = df["volume"].fillna(1).replace(0, 1)
    df["close"] = df["close"].fillna(1).replace(0, 1)

    df["flow"] = df["foreign_net"] + df["inst_net"]
    df["turnover"] = df["close"] * df["volume"]

    df["flow_ratio"] = df["flow"] / (df["turnover"] + EPS)
    df["flow_ratio"] = winsorize(df["flow_ratio"])
    df["flow_neutral"] = df["flow_ratio"] - df["flow_ratio"].mean()
    df["flow_z"] = zscore(df["flow_neutral"])
    df["mom_z"] = zscore(df["mom"])
    df["dart_z"] = zscore(df["dart_score"])

    df["score"] = (
        0.5 * df["flow_z"] +
        0.3 * df["mom_z"] +
        0.2 * df["dart_z"]
    )
    return df

# =========================
# 5. HISTORY - LATEST ONLY
# =========================
def update_history(df, date_str):
    try:
        today = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except:
        today = datetime.now().strftime("%Y-%m-%d")

    cols = ["code", "close"]
    if "score" in df.columns:
        cols.append("score")

    # 종목별 최신 행만 추출 - 날짜 덮어쓰기 방지
    latest = df.sort_values(["code", "date"]).groupby("code").tail(1)[cols].copy() if "date" in df.columns else df[cols].copy()
    latest["date"] = today

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)
        hist["date"] = hist["date"].astype(str)
        hist = pd.concat([hist, latest], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = latest

    hist.to_csv(HISTORY_PATH, index=False)
    return hist

# =========================
# 6. ROLLING IC - SHIFT SAFE
# =========================
def compute_ic_series(hist, window=IC_WINDOW):
    if hist is None or "score" not in hist.columns:
        print("[IC] score 컬럼 없음 → 누적 필요")
        return None

    h = hist.sort_values(["code", "date"]).copy()
    h["code"] = h["code"].astype(str).str.zfill(6)

    # 익일 수익률 계산 - shift(-1) 사용
    h["next_return"] = h.groupby("code")["close"].shift(-1) / h["close"] - 1

    dates = sorted(h["date"].unique())
    if len(dates) < 2:
        print("[IC] 날짜 2일 미만 → 스킵")
        return None

    ic_list = []
    for i in range(len(dates) - 1):
        t0 = dates[i]
        t0_data = h[h["date"] == t0][["code", "score", "next_return"]].dropna()

        if len(t0_data) >= MIN_IC_SAMPLE:
            ic = t0_data["score"].corr(t0_data["next_return"])
            if ic is not None and not np.isnan(ic):
                ic_list.append(ic)

    if len(ic_list) == 0:
        print("[IC] 계산 가능한 날짜 쌍 없음")
        return None

    if len(ic_list) < window:
        rolling_ic = float(pd.Series(ic_list).mean())
        print(f"[IC] Rolling({len(ic_list)}일) = {rolling_ic:.4f} (누적 중)")
    else:
        rolling_ic = float(pd.Series(ic_list).tail(window).mean())
        print(f"[IC] Rolling({window}일) = {rolling_ic:.4f}")

    return rolling_ic

# =========================
# 7. NORMALIZE
# =========================
def normalize_score(df):
    if len(df) == 0:
        return df
    s_min = df["score"].min()
    s_max = df["score"].max()
    if s_max - s_min > EPS:
        df["score_norm"] = ((df["score"] - s_min) / (s_max - s_min) * 100).round(2)
    else:
        df["score_norm"] = 50.0
    return df

# =========================
# MAIN ENGINE
# =========================
def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    # 1. data.json 로드
    df, date_str = load_data()
    if df.empty:
        print("[FATAL] data.json 비어있음")
        return

    # 2. DART
    dart_df = fetch_dart_score(date_str)
    if len(dart_df) > 0:
        df = df.merge(dart_df, on="code", how="left")
    else:
        df["dart_score"] = 0.0 # ← SSOT 패딩

    # 3. 히스토리 로드
    hist_prev = pd.read_csv(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None

    # 4. 팩터 계산
    df = compute_momentum(df, hist_prev)
    df = compute_score(df)

    # 5. 히스토리 저장
    hist = update_history(df, date_str)

    # 6. IC
    ic = compute_ic_series(hist, window=IC_WINDOW)

    # 7. TOP N
    df_top = (
        df.sort_values("score", ascending=False)
       .head(TOP_N)
       .reset_index(drop=True)
    )
    df_top = normalize_score(df_top)

    cols = [c for c in [
        "code", "name", "close", "score_norm",
        "flow_z", "mom_z", "dart_z",
        "foreign_net", "inst_net", "dart_score", "volume"
    ] if c in df_top.columns]

    records = df_top[cols].rename(columns={"score_norm": "score"}).to_dict("records")
    for r in records:
        r["code"] = str(r["code"]).zfill(6)

    result = {
        "version": ENGINE_VERSION,
        "biz_day": date_str,
        "ic": None if (ic is None or (isinstance(ic, float) and np.isnan(ic))) else ic,
        "ic_window": IC_WINDOW,
        "count": int(len(df)),
        "top10": records
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[TOP1] {records[0].get('name', records[0]['code'])} / score {records[0]['score']}")
    print(f"[ENGINE DONE] {ENGINE_VERSION}")

if __name__ == "__main__":
    run_engine()
