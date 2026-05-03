"""
engine/engine.py — v24.6 FINAL
fetch_data.py → data.json → engine.py → result.json + history.csv + ic_log.json
"""

import json
import math
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "data.json")
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH = os.path.join(ROOT, "result.json")
IC_LOG_PATH = os.path.join(ROOT, "ic_log.json")

ENGINE_VERSION = "v24.6_FINAL"
TOP_N = 10
MIN_IC_SAMPLE = 30
IC_WINDOW = 5
MOM_WINDOW = 5 # 20→5로 변경: 20일치 데이터라 MA5만 가능
FUTURE_WINDOW = 5
RETURN_20D_WINDOW = 5 # 20→5로 변경
EPS = 1e-9


def zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    std = s.std(ddof=1)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std

def winsorize(s: pd.Series, p: float = 0.01) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").fillna(0.0)
    return s.clip(s.quantile(p), s.quantile(1 - p))

def safe_corr(x: pd.Series, y: pd.Series):
    idx = x.dropna().index.intersection(y.dropna().index)
    x, y = x.loc[idx], y.loc[idx]
    if len(x) < MIN_IC_SAMPLE or x.std(ddof=1) == 0 or y.std(ddof=1) == 0:
        return None
    return float(x.corr(y))

def nan_to_null(obj):
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: nan_to_null(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [nan_to_null(v) for v in obj]
    return obj


def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"]).replace([np.inf, -np.inf], np.nan)
    df["code"] = df["code"].astype(str).str.zfill(6)

    if "name" not in df.columns:
        df["name"] = ""
    else:
        df["name"] = df["name"].fillna("")

    print(f"[DATA] {len(df)}개 종목 로드 / 기준일: {raw.get('date', '?')}")
    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))


def fetch_dart_score(date_str: str) -> pd.DataFrame:
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        print(" [DART] API 키 없음 → skip")
        return pd.DataFrame(columns=["code", "dart_score"])

    try:
        end = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = (end - timedelta(days=20)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": dart_key,
            "bgn_de": start,
            "end_de": end_s,
            "page_count": 100,
        }
        data = requests.get(url, params=params, timeout=10).json()

        status = data.get("status")
        count = len(data.get("list", []))
        print(f" [DART] status={status} / 공시수={count} / 기간={start}~{end_s}")

        if status!= "000" or count == 0:
            return pd.DataFrame(columns=["code", "dart_score"])

        dart_df = pd.DataFrame(data["list"])
        positive = ["배당결정", "자사주취득", "영업이익증가", "수주계약", "공급계약", "흑자전환", "실적개선", "매출증가", "신규상장", "유상증자철회", "투자유치", "기술제휴", "특허등록", "제품출시", "공장증설"]
        negative = ["소송제기", "영업손실", "적자전환", "계약해지", "계약취소", "매출감소", "상장폐지", "불성실공시", "횡령", "배임", "감사거절", "부도", "파산신청", "영업정지", "리콜"]

        def score_report(title: str) -> float:
            pos = sum(1 for k in positive if k in str(title))
            neg = sum(1 for k in negative if k in str(title))
            return float(pos - neg)

        dart_df["dart_score"] = dart_df["report_nm"].apply(score_report)
        dart_df = dart_df[dart_df["stock_code"].notna()]
        dart_df = dart_df[dart_df["stock_code"].astype(str).str.strip()!= ""]
        dart_df = dart_df.rename(columns={"stock_code": "code"})
        dart_df["code"] = dart_df["code"].astype(str).str.zfill(6)

        result = dart_df.groupby("code")["dart_score"].mean().round(4).reset_index()
        scored = (result["dart_score"].abs() > 0).sum()
        print(f" [DART] 점수 부여 종목: {scored}개")
        return result

    except Exception as e:
        print(f" [DART] 오류: {e}")
        return pd.DataFrame(columns=["code", "dart_score"])


def compute_hist_features(hist_df) -> pd.DataFrame:
    """20일치 데이터가 없으면 0 반환"""
    empty = pd.DataFrame(columns=["code", "mom", "return_5d", "return_20d"])

    if hist_df is None or len(hist_df) < 5:
        return empty

    h = hist_df.sort_values(["code", "date"]).copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["close"] = pd.to_numeric(h["close"], errors="coerce")

    h["ret"] = h.groupby("code")["close"].transform(lambda x: x.pct_change())
    h["mom"] = h.groupby("code")["ret"].transform(lambda x: x.rolling(MOM_WINDOW, min_periods=1).mean())
    h["return_5d"] = h.groupby("code")["close"].transform(lambda x: x.pct_change(FUTURE_WINDOW))
    h["return_20d"] = h.groupby("code")["close"].transform(lambda x: x.pct_change(RETURN_20D_WINDOW))

    return h.groupby("code")[["mom", "return_5d", "return_20d"]].last().reset_index()


def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    df["foreign_net"] = pd.to_numeric(df["foreign_net"] if "foreign_net" in df.columns else 0, errors="coerce").fillna(0)
    df["inst_net"] = pd.to_numeric(df["inst_net"] if "inst_net" in df.columns else 0, errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(1).replace(0, 1)
    df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(1).replace(0, 1)
    df["mom"] = df["mom"].fillna(0.0) if "mom" in df.columns else 0.0
    df["dart_score"] = df["dart_score"].fillna(0.0) if "dart_score" in df.columns else 0.0

    df["flow"] = df["foreign_net"] + df["inst_net"]
    df["turnover"] = df["close"] * df["volume"]
    df["flow_ratio"] = winsorize(df["flow"] / (df["turnover"] + EPS))
    df["flow_neutral"] = df["flow_ratio"] - df["flow_ratio"].mean()

    df["flow_z"] = zscore(df["flow_neutral"])
    df["mom_z"] = zscore(df["mom"])
    df["dart_z"] = zscore(df["dart_score"])
    df["score"] = 0.5 * df["flow_z"] + 0.3 * df["mom_z"] + 0.2 * df["dart_z"]
    return df


def update_history(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    today = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    df_top = df.sort_values("score", ascending=False).head(TOP_N).copy()
    df_top["date"] = today

    cols = ["date", "code", "close", "score", "mom_z", "flow_z", "dart_z", "return_5d", "return_20d"]
    for c in cols:
        if c not in df_top.columns:
            df_top[c] = 0.0

    new_rows = df_top[cols]

    if os.path.exists(HISTORY_PATH):
        existing = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
        combined.to_csv(HISTORY_PATH, index=False)
    else:
        new_rows.to_csv(HISTORY_PATH, index=False)

    return df_top


def compute_ic_series(hist: pd.DataFrame, window: int = IC_WINDOW):
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

    if not ic_list:
        return None

    return float(pd.Series(ic_list).tail(window).mean())


def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    df, date_str = load_data()
    if df.empty:
        print("[ERROR] data.json 비어있음")
        return

    dart_df = fetch_dart_score(date_str)
    if not dart_df.empty and "dart_score" in dart_df.columns:
        df = df.merge(dart_df, on="code", how="left")
    df["dart_score"] = df["dart_score"].fillna(0.0) if "dart_score" in df.columns else 0.0

    hist_prev = pd.read_csv(HISTORY_PATH, dtype={"code": str}) if os.path.exists(HISTORY_PATH) else None
    hist_feat = compute_hist_features(hist_prev)
    df = df.merge(hist_feat, on="code", how="left")

    df = compute_score(df)
    update_history(df, date_str)

    ic = None
    if os.path.exists(HISTORY_PATH):
        hist_full = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        hist_60d = hist_full[hist_full["date"] >= cutoff]

        if len(hist_60d) >= MIN_IC_SAMPLE:
            ic = compute_ic_series(hist_60d)
            if ic is not None:
                ic_entry = {
                    "date": date_str,
                    "ic_score": round(ic, 4),
                    "ic_mom": round(safe_corr(hist_60d["mom_z"], hist_60d["return_5d"]) or 0, 4),
                    "ic_flow": round(safe_corr(hist_60d["flow_z"], hist_60d["return_5d"]) or 0, 4),
                    "ic_dart": round(safe_corr(hist_60d["dart_z"], hist_60d["return_5d"]) or 0, 4),
                }
                ic_log = []
                if os.path.exists(IC_LOG_PATH):
                    with open(IC_LOG_PATH, "r", encoding="utf-8") as f:
                        ic_log = json.load(f)
                    if not isinstance(ic_log, list):
                        ic_log = [ic_log]
                ic_log.append(ic_entry)
                with open(IC_LOG_PATH, "w", encoding="utf-8") as f:
                    json.dump(ic_log, f, ensure_ascii=False, indent=2)

    smin, smax = df["score"].min(), df["score"].max()
    if smax - smin < EPS:
        df["score_norm"] = 50.0
    else:
        df["score_norm"] = ((df["score"] - smin) / (smax - smin) * 100).round(2)

    df_top = df.sort_values("score", ascending=False).head(TOP_N).reset_index(drop=True)

    want_cols = ["code", "name", "close", "score_norm", "flow_z", "mom_z", "dart_z", "foreign_net", "inst_net", "dart_score", "volume", "return_5d", "return_20d"]
    out_cols = [c for c in want_cols if c in df_top.columns]
    records = df_top[out_cols].rename(columns={"score_norm": "score"}).to_dict("records")
    records = json.loads(json.dumps(records, default=nan_to_null))

    result = {
        "version": ENGINE_VERSION,
        "biz_day": date_str,
        "ic": round(ic, 4) if ic is not None else None,
        "ic_window": IC_WINDOW,
        "count": len(df),
        "top10": records,
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[ENGINE DONE] TOP1: {records[0].get('name', records[0]['code'])} / score {records[0]['score']}")


if __name__ == "__main__":
    run_engine()
