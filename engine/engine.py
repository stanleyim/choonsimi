"""
engine/engine.py — v24.5 FINAL
fetch_data.py → data.json → engine.py → result.json + history.csv + ic_log.json

수정 내역 (v24.4 → v24.5):
  1. df.get("mom", 0) AttributeError 수정
  2. return_5d/20d shift(-N) 미래 데이터 → 과거 수익률로 전환
  3. cols 미사용 버그 수정 → name 없을 시 KeyError 방지
  4. compute_hist_features rolling alignment → groupby transform
  5. history.csv 중복 누적 → drop_duplicates 추가
  6. ic_log.json 덮어쓰기 → append 누적 구조
  7. name 컬럼 안전 처리
"""

import json
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH    = os.path.join(ROOT, "data.json")
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH  = os.path.join(ROOT, "result.json")
IC_LOG_PATH  = os.path.join(ROOT, "ic_log.json")

ENGINE_VERSION    = "v24.5_FINAL"
TOP_N             = 10
MIN_IC_SAMPLE     = 30
IC_WINDOW         = 5
MOM_WINDOW        = 20
FUTURE_WINDOW     = 5
RETURN_20D_WINDOW = 20
EPS               = 1e-9


# =========================
# UTIL
# =========================
def zscore(series: pd.Series) -> pd.Series:
    s   = pd.to_numeric(series, errors="coerce").fillna(0.0)
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


# =========================
# 1. DATA LOAD
# =========================
def load_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    df = pd.DataFrame(raw["all"]).replace([np.inf, -np.inf], np.nan)
    df["code"] = df["code"].astype(str).str.zfill(6)

    # ★ name 컬럼 안전 처리
    if "name" not in df.columns:
        df["name"] = ""
    else:
        df["name"] = df["name"].fillna("")

    print(f"[DATA] {len(df)}개 종목 로드 / 기준일: {raw.get('date', '?')}")
    return df, raw.get("date", datetime.now().strftime("%Y-%m-%d"))


# =========================
# 2. DART
# =========================
def fetch_dart_score(date_str: str) -> pd.DataFrame:
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        return pd.DataFrame(columns=["code", "dart_score"])

    try:
        end   = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = (end - timedelta(days=7)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        url    = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": dart_key,
            "bgn_de":    start,
            "end_de":    end_s,
            "page_count": 100,
        }
        data = requests.get(url, params=params, timeout=10).json()

        if data.get("status") != "000" or not data.get("list"):
            return pd.DataFrame(columns=["code", "dart_score"])

        dart_df  = pd.DataFrame(data["list"])
        positive = ["배당", "자사주", "실적", "수주", "계약", "증가", "흑자"]
        negative = ["소송", "적자", "손실", "감소", "취소", "지연"]

        dart_df["dart_score"] = dart_df["report_nm"].apply(
            lambda t: sum(1 for k in positive if k in str(t))
                    - sum(1 for k in negative if k in str(t))
        )
        return (dart_df.rename(columns={"stock_code": "code"})
                       .groupby("code")["dart_score"]
                       .sum()
                       .reset_index())

    except Exception:
        return pd.DataFrame(columns=["code", "dart_score"])


# =========================
# 3. HIST FEATURES
# =========================
def compute_hist_features(hist_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    ★ 수정: shift(-N) 미래 데이터 제거 → 과거 수익률로 전환
    ★ 수정: rolling alignment → groupby transform
    """
    empty = pd.DataFrame(columns=["code", "mom", "return_5d", "return_20d"])

    if hist_df is None or len(hist_df) < 5:
        return empty

    h = hist_df.sort_values(["code", "date"]).copy()
    h["code"]  = h["code"].astype(str).str.zfill(6)
    h["close"] = pd.to_numeric(h["close"], errors="coerce")

    # ★ groupby transform → 종목별 독립 rolling (alignment 안전)
    h["ret"] = h.groupby("code")["close"].transform(
        lambda x: x.pct_change()
    )
    h["mom"] = h.groupby("code")["ret"].transform(
        lambda x: x.rolling(MOM_WINDOW, min_periods=1).mean()
    )

    # ★ 과거 수익률 (미래 데이터 사용 제거)
    # return_5d  = 최근 5일 수익률  (현재 / 5일 전 종가 - 1)
    # return_20d = 최근 20일 수익률
    h["return_5d"] = h.groupby("code")["close"].transform(
        lambda x: x.pct_change(FUTURE_WINDOW)
    )
    h["return_20d"] = h.groupby("code")["close"].transform(
        lambda x: x.pct_change(RETURN_20D_WINDOW)
    )

    return h.groupby("code")[["mom", "return_5d", "return_20d"]].last().reset_index()


# =========================
# 4. SCORE
# =========================
def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    df["foreign_net"] = pd.to_numeric(df.get("foreign_net", 0), errors="coerce").fillna(0)
    df["inst_net"]    = pd.to_numeric(df.get("inst_net",    0), errors="coerce").fillna(0)
    df["volume"]      = pd.to_numeric(df["volume"], errors="coerce").fillna(1).replace(0, 1)
    df["close"]       = pd.to_numeric(df["close"],  errors="coerce").fillna(1).replace(0, 1)

    # ★ df.get() AttributeError 수정
    df["mom"]        = df["mom"].fillna(0.0)        if "mom"        in df.columns else 0.0
    df["dart_score"] = df["dart_score"].fillna(0.0) if "dart_score" in df.columns else 0.0

    df["flow"]         = df["foreign_net"] + df["inst_net"]
    df["turnover"]     = df["close"] * df["volume"]
    df["flow_ratio"]   = winsorize(df["flow"] / (df["turnover"] + EPS))
    df["flow_neutral"] = df["flow_ratio"] - df["flow_ratio"].mean()

    df["flow_z"] = zscore(df["flow_neutral"])
    df["mom_z"]  = zscore(df["mom"])
    df["dart_z"] = zscore(df["dart_score"])
    df["score"]  = 0.5 * df["flow_z"] + 0.3 * df["mom_z"] + 0.2 * df["dart_z"]
    return df


# =========================
# 5. HISTORY
# =========================
def update_history(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    ★ 수정: 같은 날 중복 실행 시 중복 누적 방지 (drop_duplicates)
    """
    today  = datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    df_top = df.sort_values("score", ascending=False).head(TOP_N).copy()
    df_top["date"] = today

    cols = ["date", "code", "close", "score",
            "mom_z", "flow_z", "dart_z", "return_5d", "return_20d"]

    # 없는 컬럼은 0으로 채움
    for c in cols:
        if c not in df_top.columns:
            df_top[c] = 0.0

    new_rows = df_top[cols]

    if os.path.exists(HISTORY_PATH):
        existing = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        combined = pd.concat([existing, new_rows], ignore_index=True)
        # ★ 중복 방지: 같은 (date, code) 마지막 값 유지
        combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
        combined.to_csv(HISTORY_PATH, index=False)
    else:
        new_rows.to_csv(HISTORY_PATH, index=False)

    return df_top


# =========================
# 6. ROLLING IC
# =========================
def compute_ic_series(hist: pd.DataFrame, window: int = IC_WINDOW):
    if hist is None or len(hist) < MIN_IC_SAMPLE:
        return None

    dates   = sorted(hist["date"].unique())
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


# =========================
# MAIN ENGINE
# =========================
def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    df, date_str = load_data()
    if df.empty:
        print("[ERROR] data.json 비어있음")
        return

    # DART
    dart_df = fetch_dart_score(date_str)
    if len(dart_df) > 0:
        df = df.merge(dart_df, on="code", how="left")
    else:
        df["dart_score"] = 0.0

    # HIST FEATURES
    hist_prev = pd.read_csv(HISTORY_PATH, dtype={"code": str}) \
                if os.path.exists(HISTORY_PATH) else None
    hist_feat = compute_hist_features(hist_prev)
    df = df.merge(hist_feat, on="code", how="left")

    # SCORE
    df = compute_score(df)

    # HISTORY 저장
    update_history(df, date_str)

    # IC 계산
    ic = None
    if os.path.exists(HISTORY_PATH):
        hist_full = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        cutoff    = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        hist_60d  = hist_full[hist_full["date"] >= cutoff]

        if len(hist_60d) >= MIN_IC_SAMPLE:
            ic = compute_ic_series(hist_60d)

            if ic is not None:
                ic_entry = {
                    "date":     date_str,
                    "ic_score": round(ic, 4),
                    "ic_mom":   round(safe_corr(hist_60d["mom_z"],  hist_60d["return_5d"]) or 0, 4),
                    "ic_flow":  round(safe_corr(hist_60d["flow_z"], hist_60d["return_5d"]) or 0, 4),
                    "ic_dart":  round(safe_corr(hist_60d["dart_z"], hist_60d["return_5d"]) or 0, 4),
                }

                # ★ ic_log.json append 누적 구조 (덮어쓰기 → 이력 유지)
                ic_log = []
                if os.path.exists(IC_LOG_PATH):
                    with open(IC_LOG_PATH, "r", encoding="utf-8") as f:
                        ic_log = json.load(f)
                    if not isinstance(ic_log, list):
                        ic_log = [ic_log]  # 기존 단일 dict 호환

                ic_log.append(ic_entry)

                with open(IC_LOG_PATH, "w", encoding="utf-8") as f:
                    json.dump(ic_log, f, ensure_ascii=False, indent=2)

    # SCORE 정규화 0~100
    smin, smax = df["score"].min(), df["score"].max()
    if smax - smin < EPS:
        df["score_norm"] = 50.0
    else:
        df["score_norm"] = ((df["score"] - smin) / (smax - smin) * 100).round(2)

    # TOP10 추출
    df_top = df.sort_values("score", ascending=False).head(TOP_N).reset_index(drop=True)

    # ★ 컬럼 안전 추출 (없는 컬럼 제외)
    want_cols = ["code", "name", "close", "score_norm",
                 "flow_z", "mom_z", "dart_z",
                 "foreign_net", "inst_net", "dart_score",
                 "volume", "return_5d", "return_20d"]
    out_cols  = [c for c in want_cols if c in df_top.columns]
    records   = df_top[out_cols].rename(columns={"score_norm": "score"}).to_dict("records")

    # RESULT 저장
    result = {
        "version":   ENGINE_VERSION,
        "biz_day":   date_str,
        "ic":        round(ic, 4) if ic is not None else None,
        "ic_window": IC_WINDOW,
        "count":     len(df),
        "top10":     records,
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[ENGINE DONE] TOP1: {records[0].get('name', records[0]['code'])} "
          f"/ score {records[0]['score']}")


if __name__ == "__main__":
    run_engine()
