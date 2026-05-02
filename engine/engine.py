"""
engine.py — v16.3 FINAL
- Flow 누적 (과거 + 오늘)
- Missing penalty (감산형)
- Adaptive Weight
- Momentum (5d - 20d)
- Bootstrap (cold start 해결)
- History 정규화 (핵심)
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
FLOW_WINDOW   = 3
IC_WINDOW     = 5

KR_HOLIDAYS_2026 = {
    "20260101","20260127","20260128","20260129","20260301",
    "20260501","20260505","20260525","20260606","20260815",
    "20260924","20260925","20260926","20261003","20261009","20261225",
}

# =========================
# UTIL
# =========================
def zscore(s):
    std = s.std()
    if std == 0 or np.isnan(std):
        return s * 0.0
    return (s - s.mean()) / std

def winsorize(s, p=0.01):
    return s.clip(s.quantile(p), s.quantile(1-p))

def get_last_business_day():
    d = datetime.now()
    while True:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5 and ds not in KR_HOLIDAYS_2026:
            return ds
        d -= timedelta(days=1)

# =========================
# BOOTSTRAP (핵심)
# =========================
def bootstrap_history(df):
    if os.path.exists(HISTORY_FILE):
        hist = pd.read_csv(HISTORY_FILE)
        if len(hist) > 1000:
            return

    print("[BOOTSTRAP] 초기 히스토리 생성")

    df["code"] = df["code"].astype(str).str.zfill(6)
    today = pd.Timestamp.now()

    rows = []
    for i in range(5):
        d = (today - pd.Timedelta(days=5-i)).strftime("%Y-%m-%d")
        tmp = df[["code", "close"]].copy()
        tmp["foreign_net"] = 0
        tmp["inst_net"] = 0
        tmp["score"] = 0
        tmp["date"] = d
        rows.append(tmp)

    pd.concat(rows).to_csv(HISTORY_FILE, index=False)
    print("[BOOTSTRAP DONE]")

# =========================
# FLOW
# =========================
def fetch_flow(date_str):
    try:
        f = stock.get_market_net_purchases_of_equities(date_str, date_str, "KOSPI", "외국인")
        i = stock.get_market_net_purchases_of_equities(date_str, date_str, "KOSPI", "기관합계")

        if f is None or len(f)==0: f = pd.DataFrame(columns=["티커","순매수거래대금"])
        if i is None or len(i)==0: i = pd.DataFrame(columns=["티커","순매수거래대금"])

        f = f.reset_index().rename(columns={"티커":"code","순매수거래대금":"foreign_net"})
        i = i.reset_index().rename(columns={"티커":"code","순매수거래대금":"inst_net"})

        f["code"] = f["code"].astype(str).str.zfill(6)
        i["code"] = i["code"].astype(str).str.zfill(6)

        flow_df = pd.merge(
            f[["code","foreign_net"]],
            i[["code","inst_net"]],
            on="code",
            how="outer"
        ).fillna(0)

        codes = set(flow_df["code"])
        print(f"[FLOW] {len(flow_df)}개 종목")
        return flow_df, codes

    except Exception as e:
        print("[FLOW ERROR]", e)
        return pd.DataFrame(columns=["code","foreign_net","inst_net"]), set()

def compute_flow(df, date, hist):
    flow_df, codes = fetch_flow(date)

    df["code"] = df["code"].astype(str).str.zfill(6)
    df["flow_missing"] = ~df["code"].isin(codes)

    coverage = len(codes) / len(df) if len(df)>0 else 0
    print(f"[FLOW] coverage {coverage:.2f}")

    if len(flow_df)>0:
        df = df.set_index("code")
        flow_df = flow_df.set_index("code")

        mask = df.index.isin(flow_df.index)
        df.loc[mask,"foreign_net"] = flow_df.loc[df.loc[mask].index,"foreign_net"]
        df.loc[mask,"inst_net"]    = flow_df.loc[df.loc[mask].index,"inst_net"]
        df = df.reset_index()

    df["flow_raw"] = df["foreign_net"] + df["inst_net"]

    if hist is not None and len(hist)>=2:
        h = hist.sort_values(["code","date"])
        h["flow"] = h["foreign_net"].fillna(0)+h["inst_net"].fillna(0)

        prev = (
            h.groupby("code")["flow"]
            .rolling(FLOW_WINDOW-1, min_periods=1)
            .sum()
            .groupby("code")
            .last()
        )

        df["flow_3d"] = df["code"].map(prev).fillna(0)+df["flow_raw"]
    else:
        df["flow_3d"] = df["flow_raw"]

    return df, coverage

# =========================
# MOMENTUM
# =========================
def compute_mom(df, hist):
    if hist is None or len(hist)<20:
        df["mom"]=0
        return df

    h = hist.sort_values(["code","date"])
    h["r5"]  = h.groupby("code")["close"].pct_change(5)
    h["r20"] = h.groupby("code")["close"].pct_change(20)
    h["mom"] = h["r5"]-h["r20"]

    df["mom"] = df["code"].map(h.groupby("code")["mom"].last()).fillna(0)
    return df

# =========================
# SCORE
# =========================
def compute_score(df, coverage):
    df["turnover"] = df["close"]*df["volume"].replace(0,1)

    df["flow_z"] = zscore(winsorize((df["flow_3d"]/df["turnover"]).clip(-1,1)))

    penalty = max(0,1-coverage)
    df.loc[df["flow_missing"],"flow_z"] -= penalty

    df["mom_z"]  = zscore(winsorize(df["mom"]))
    df["dart_z"] = zscore(winsorize(df["dart_score"]))

    flow_w = 0.6*coverage
    mom_w  = 0.3+(0.6-flow_w)
    dart_w = 0.1

    print(f"[WEIGHT] {flow_w:.2f}/{mom_w:.2f}/{dart_w:.2f}")

    df["score"] = flow_w*df["flow_z"] + mom_w*df["mom_z"] + dart_w*df["dart_z"]
    return df

# =========================
# HISTORY
# =========================
def update_history(df):
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    df["code"] = df["code"].astype(str).str.zfill(6)

    new = df[["code","close","foreign_net","inst_net","score"]].copy()
    new["date"] = today

    if os.path.exists(HISTORY_FILE):
        hist = pd.read_csv(HISTORY_FILE)
        hist["code"] = hist["code"].astype(str).str.zfill(6)
        hist = pd.concat([hist,new]).drop_duplicates(["code","date"])
    else:
        hist = new

    hist = hist.sort_values(["code","date"])
    hist.to_csv(HISTORY_FILE,index=False)
    return hist

# =========================
# IC
# =========================
def compute_ic(hist):
    if hist is None or "score" not in hist.columns:
        return None

    h = hist.sort_values(["code","date"])
    dates = sorted(h["date"].unique())

    ic_list = []

    for i in range(1,len(dates)):
        t0,t1 = dates[i-1],dates[i]

        s = h[h["date"]==t0].set_index("code")["score"]
        c0 = h[h["date"]==t0].set_index("code")["close"]
        c1 = h[h["date"]==t1].set_index("code")["close"]

        ret = c1/c0-1
        m = pd.concat([s,ret],axis=1).dropna()

        if len(m)>=MIN_IC_SAMPLE:
            ic_list.append(m.corr().iloc[0,1])

    if len(ic_list)==0:
        return None

    return float(pd.Series(ic_list).tail(IC_WINDOW).mean())

# =========================
# RUN
# =========================
def run():
    print("[ENGINE v16.3 FINAL START]")

    date = get_last_business_day()
    print("[DATE]", date)

    with open(DATA_FILE) as f:
        df = pd.DataFrame(json.load(f)["all"])

    bootstrap_history(df)

    hist_prev = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else None

    df, coverage = compute_flow(df, date, hist_prev)
    df = compute_mom(df, hist_prev)
    df = compute_score(df, coverage)

    hist = update_history(df)
    ic = compute_ic(hist)

    top = df.sort_values("score",ascending=False).head(TOP_N)

    result = {
        "top10": top.to_dict("records"),
        "ic": None if ic is None else round(ic,4),
        "flow_coverage": round(coverage,3),
        "count": len(df),
        "biz_day": date
    }

    with open(RESULT_FILE,"w") as f:
        json.dump(result,f,indent=2)

    print("[TOP1]", top.iloc[0]["name"] if len(top)>0 else "N/A")
    print("[IC]", result["ic"])
    print("[ENGINE DONE]")

if __name__ == "__main__":
    run()
