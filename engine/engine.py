"""
engine.py — v16 FINAL (ROLLING IC + FLOW NEUTRAL + MARKET NEUTRAL FIXED)
"""

import json
import os
import warnings
import numpy as np
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", category=RuntimeWarning)

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE    = os.path.join(ROOT, "data.json")
HISTORY_FILE = os.path.join(ROOT, "history.csv")
RESULT_FILE  = os.path.join(ROOT, "result.json")

TOP_N         = 10
MIN_IC_SAMPLE = 30
MOM_WINDOW    = 5
IC_WINDOW     = 5   # ✅ Rolling IC window

# 2026 한국 공휴일
KR_HOLIDAYS_2026 = {
    "20260101", "20260127", "20260128", "20260129",
    "20260301", "20260501", "20260505", "20260525",
    "20260606", "20260815", "20260924", "20260925",
    "20260926", "20261003", "20261009", "20261225",
}


# =========================
# UTIL
# =========================
def safe_corr(x, y):
    idx = x.dropna().index.intersection(y.dropna().index)
    x, y = x.loc[idx], y.loc[idx]
    if len(x) < MIN_IC_SAMPLE:
        return None
    if x.std() == 0 or y.std() == 0:
        return None
    return float(x.corr(y))


def zscore(series):
    std = series.std()
    if std == 0 or np.isnan(std):
        return series * 0.0
    return (series - series.mean()) / std


def winsorize(s, p=0.01):
    lower = s.quantile(p)
    upper = s.quantile(1 - p)
    return s.clip(lower, upper)


def get_last_business_day():
    d = datetime.now()
    while True:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5 and ds not in KR_HOLIDAYS_2026:
            try:
                test = stock.get_market_net_purchases_of_equities_by_ticker(
                    ds, ds, "KOSPI", "외국인"
                )
                if test is not None and len(test) > 0:
                    return ds
            except:
                pass
        d -= timedelta(days=1)


# =========================
# FLOW (KRX)
# =========================
def fetch_flow(date_str):
    try:
        foreign = stock.get_market_net_purchases_of_equities_by_ticker(
            date_str, date_str, "KOSPI", "외국인"
        )
        foreign = foreign[["순매수거래대금"]].rename(
            columns={"순매수거래대금": "foreign_net"}
        )

        inst = stock.get_market_net_purchases_of_equities_by_ticker(
            date_str, date_str, "KOSPI", "기관합계"
        )
        inst = inst[["순매수거래대금"]].rename(
            columns={"순매수거래대금": "inst_net"}
        )

        result = foreign.join(inst, how="outer")
        result.index.name = "code"
        result = result.reset_index()
        result["code"] = result["code"].astype(str).str.zfill(6)

        print(f"[FLOW] {len(result)}개 종목 수급 수집 완료")
        return result

    except Exception as e:
        print("[FLOW ERROR]", e)
        return None


# =========================
# HISTORY
# =========================
def update_history(df):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    cols = ["code", "close"]
    if "score" in df.columns:
        cols.append("score")

    new = df[cols].copy()
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
def compute_flow_data(df, date_str):
    flow_df = fetch_flow(date_str)

    if flow_df is not None:
        df["code"] = df["code"].astype(str).str.zfill(6)
        df = df.merge(flow_df, on="code", how="left")
        print(f"[FLOW] foreign_net 유효값: {df['foreign_net'].notna().sum()}개")
    else:
        df["foreign_net"] = np.nan
        df["inst_net"]    = np.nan
        print("[FLOW] 수급 없음 → NaN 유지")

    return df


def compute_momentum(df, hist):
    if hist is None or len(hist) < 10:
        df["mom"] = 0.0
        return df

    h = hist.sort_values(["code", "date"]).copy()
    h["ret"] = h.groupby("code")["close"].pct_change()

    h["mom"] = (
        h.groupby("code")["ret"]
        .rolling(MOM_WINDOW, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    mom = h.groupby("code")["mom"].last()
    df["mom"] = df["code"].map(mom)
    return df


# ✅ Fix 2: flow_ratio → 시장 중립화
def compute_score(df):
    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"]    = df["inst_net"].fillna(0)
    df["mom"]         = df["mom"].fillna(0)
    df["dart_score"]  = df["dart_score"].fillna(0)
    df["volume"]      = df["volume"].fillna(1).replace(0, 1)
    df["close"]       = df["close"].fillna(1).replace(0, 1)

    df["flow"]        = df["foreign_net"] + df["inst_net"]
    df["turnover"]    = df["close"] * df["volume"]

    # flow ratio
    df["flow_ratio"]  = df["flow"] / df["turnover"]
    df["flow_ratio"]  = winsorize(df["flow_ratio"])

    # ✅ 시장 중립화 (market neutralization)
    df["flow_neutral"] = df["flow_ratio"] - df["flow_ratio"].mean()
    df["flow_z"]       = zscore(df["flow_neutral"])

    df["mom_z"]  = zscore(df["mom"])
    df["dart_z"] = zscore(df["dart_score"])

    df["score"] = (
        0.5 * df["flow_z"] +
        0.3 * df["mom_z"] +
        0.2 * df["dart_z"]
    )

    return df


# ✅ Fix 1: Rolling IC (window=5)
def compute_ic_series(hist, window=IC_WINDOW):
    if hist is None or "score" not in hist.columns:
        print("[IC] score 컬럼 없음 → 누적 필요")
        return None

    h = hist.sort_values(["code", "date"]).copy()
    dates = sorted(h["date"].unique())

    if len(dates) < 2:
        print("[IC] 날짜 2일 미만 → 스킵")
        return None

    ic_list = []

    for i in range(1, len(dates)):
        t0 = dates[i - 1]
        t1 = dates[i]

        score  = h[h["date"] == t0].set_index("code")["score"]
        close0 = h[h["date"] == t0].set_index("code")["close"]
        close1 = h[h["date"] == t1].set_index("code")["close"]

        ret = (close1 / close0 - 1).rename("ret")

        merged = pd.concat([score, ret], axis=1).dropna()
        merged.columns = ["score", "ret"]

        if len(merged) >= MIN_IC_SAMPLE:
            ic_val = merged["score"].corr(merged["ret"])
            ic_list.append(ic_val)

    if len(ic_list) == 0:
        print("[IC] 계산 가능한 날짜 쌍 없음")
        return None

    if len(ic_list) < window:
        # window 미만이면 있는 것만으로 평균
        rolling_ic = float(pd.Series(ic_list).mean())
        print(f"[IC] Rolling({len(ic_list)}일) = {rolling_ic:.4f} (누적 중)")
    else:
        rolling_ic = float(pd.Series(ic_list).tail(window).mean())
        print(f"[IC] Rolling({window}일) = {rolling_ic:.4f}")

    return rolling_ic


# =========================
# NORMALIZE
# =========================
def normalize_score(df):
    s_min = df["score"].min()
    s_max = df["score"].max()
    if s_max - s_min > 0:
        df["score_norm"] = ((df["score"] - s_min) / (s_max - s_min) * 100).round(2)
    else:
        df["score_norm"] = 50.0
    return df


# =========================
# ENGINE
# =========================
def run():
    print("[ENGINE v16 START]")

    biz_day = get_last_business_day()
    print(f"[DATE] 기준일: {biz_day}")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"])
    df = df.replace([np.inf, -np.inf], np.nan)

    # 이전 history 로드 (momentum 계산용)
    hist_prev = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else None

    df = compute_flow_data(df, biz_day)
    df = compute_momentum(df, hist_prev)
    df = compute_score(df)

    # score 포함 history 저장
    hist = update_history(df)

    # ✅ Rolling IC
    ic = compute_ic_series(hist, window=IC_WINDOW)

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
        "top10":      records,
        "ic":         None if (ic is None or (isinstance(ic, float) and np.isnan(ic))) else ic,
        "ic_window":  IC_WINDOW,
        "count":      int(len(df)),
        "biz_day":    biz_day
    }

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[TOP1] {records[0].get('name', records[0]['code'])} / score {records[0]['score']}")
    print("[ENGINE v16 DONE]")


if __name__ == "__main__":
    run()
