"""
engine.py — v23 PLAN-A
pykrx KOSPI 전체 + DART + IC Rolling
로그인 불필요 / 무료 / 안정
"""

import os
import json
import warnings
import requests
import numpy as np
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# =========================
# PATHS
# =========================
ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, "history.csv")
RESULT_PATH  = os.path.join(ROOT, "result.json")

ENGINE_VERSION = "v23.0_PLAN_A"

TOP_N         = 10
MIN_IC_SAMPLE = 30
IC_WINDOW     = 5
MOM_WINDOW    = 5

KR_HOLIDAYS_2026 = {
    "20260101", "20260127", "20260128", "20260129",
    "20260301", "20260501", "20260505", "20260525",
    "20260606", "20260815", "20260924", "20260925",
    "20260926", "20261003", "20261009", "20261225",
}


# =========================
# UTIL
# =========================
def zscore(series):
    std = series.std()
    if std == 0 or np.isnan(std):
        return series * 0.0
    return (series - series.mean()) / std


def winsorize(s, p=0.01):
    lower = s.quantile(p)
    upper = s.quantile(1 - p)
    return s.clip(lower, upper)


def safe_corr(x, y):
    idx = x.dropna().index.intersection(y.dropna().index)
    x, y = x.loc[idx], y.loc[idx]
    if len(x) < MIN_IC_SAMPLE:
        return None
    if x.std() == 0 or y.std() == 0:
        return None
    return float(x.corr(y))


def get_last_business_day():
    d = datetime.now()
    while True:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5 and ds not in KR_HOLIDAYS_2026:
            return ds
        d -= timedelta(days=1)


# =========================
# 1. UNIVERSE — KOSPI 전체
# =========================
def fetch_universe(date_str):
    try:
        tickers = stock.get_market_ticker_list(date_str, market="KOSPI")
        print(f"[UNIVERSE] {len(tickers)}개 종목")
        return [str(t).zfill(6) for t in tickers]
    except Exception as e:
        print(f"[UNIVERSE ERROR] {e}")
        return []


# =========================
# 2. 가격 데이터
# =========================
def fetch_price(date_str, tickers):
    try:
        df = stock.get_market_ohlcv_by_ticker(date_str, market="KOSPI")

        if df is None or len(df) == 0:
            raise ValueError("empty price response")

        df.index.name = "code"
        df = df.reset_index()
        df["code"] = df["code"].astype(str).str.zfill(6)

        df = df.rename(columns={
            "종가": "close",
            "거래량": "volume",
            "거래대금": "turnover"
        })

        # 종목명 추가
        name_map = {}
        for t in tickers:
            try:
                name_map[t] = stock.get_market_ticker_name(t)
            except:
                name_map[t] = t

        df["name"] = df["code"].map(name_map).fillna(df["code"])

        cols = [c for c in ["code", "name", "close", "volume", "turnover"] if c in df.columns]
        df = df[cols]
        df = df[df["close"] > 0]

        print(f"[PRICE] {len(df)}개 종목 가격 수집")
        return df

    except Exception as e:
        print(f"[PRICE ERROR] {e}")
        return pd.DataFrame()


# =========================
# 3. 수급 데이터 (로그인 불필요)
# =========================
def fetch_flow(date_str):
    try:
        df = stock.get_market_trading_value_by_ticker(
            date_str, date_str, "KOSPI"
        )

        if df is None or len(df) == 0:
            raise ValueError("empty flow response")

        df.index.name = "code"
        df = df.reset_index()
        df["code"] = df["code"].astype(str).str.zfill(6)

        print("[FLOW 컬럼]", df.columns.tolist())

        # 컬럼 자동 감지
        col_map = {}
        for c in df.columns:
            if "외국인" in c and "foreign_net" not in col_map.values():
                col_map[c] = "foreign_net"
            elif "기관" in c and "inst_net" not in col_map.values():
                col_map[c] = "inst_net"

        df = df.rename(columns=col_map)

        if "foreign_net" not in df.columns:
            df["foreign_net"] = 0
        if "inst_net" not in df.columns:
            df["inst_net"] = 0

        result = df[["code", "foreign_net", "inst_net"]]
        print(f"[FLOW] {len(result)}개 종목 수급 수집 완료")
        return result

    except Exception as e:
        print(f"[FLOW ERROR] → fallback: {e}")
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])


# =========================
# 4. DART 공시 점수
# =========================
def fetch_dart_score(date_str):
    dart_key = os.environ.get("DART_API_KEY", "")
    if not dart_key:
        print("[DART] API KEY 없음 → skip")
        return pd.DataFrame(columns=["code", "dart_score"])

    try:
        # 최근 7일 공시
        end   = datetime.strptime(date_str, "%Y%m%d")
        start = (end - timedelta(days=7)).strftime("%Y%m%d")

        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": dart_key,
            "bgn_de":    start,
            "end_de":    date_str,
            "page_count": 100,
        }

        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        if data.get("status") != "000":
            raise ValueError(f"DART status: {data.get('status')}")

        items = data.get("list", [])
        if not items:
            return pd.DataFrame(columns=["code", "dart_score"])

        dart_df = pd.DataFrame(items)

        # 긍정 공시 키워드
        positive = ["배당", "자사주", "실적", "수주", "계약", "증가", "흑자"]
        negative = ["소송", "적자", "손실", "감소", "취소", "지연"]

        def score_report(title):
            s = 0
            for k in positive:
                if k in str(title):
                    s += 1
            for k in negative:
                if k in str(title):
                    s -= 1
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
# 5. MOMENTUM
# =========================
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
    df["mom"] = df["code"].map(mom).fillna(0)
    return df


# =========================
# 6. SCORE
# =========================
def compute_score(df):
    df["foreign_net"] = df["foreign_net"].fillna(0)
    df["inst_net"]    = df["inst_net"].fillna(0)
    df["mom"]         = df["mom"].fillna(0)
    df["dart_score"]  = df["dart_score"].fillna(0)
    df["volume"]      = df["volume"].fillna(1).replace(0, 1)
    df["close"]       = df["close"].fillna(1).replace(0, 1)

    df["flow"]     = df["foreign_net"] + df["inst_net"]
    df["turnover"] = df["close"] * df["volume"]

    # flow ratio → 시장 중립화
    df["flow_ratio"]   = df["flow"] / df["turnover"]
    df["flow_ratio"]   = winsorize(df["flow_ratio"])
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


# =========================
# 7. HISTORY
# =========================
def update_history(df, date_str):
    today = datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")

    cols = ["code", "close"]
    if "score" in df.columns:
        cols.append("score")

    new = df[cols].copy()
    new["date"] = today

    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH)
        hist["date"] = hist["date"].astype(str)
        hist = pd.concat([hist, new], ignore_index=True)
        hist = hist.drop_duplicates(["code", "date"])
    else:
        hist = new

    hist.to_csv(HISTORY_PATH, index=False)
    return hist


# =========================
# 8. ROLLING IC
# =========================
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

        ret    = (close1 / close0 - 1).rename("ret")
        merged = pd.concat([score, ret], axis=1).dropna()
        merged.columns = ["score", "ret"]

        if len(merged) >= MIN_IC_SAMPLE:
            ic_list.append(merged["score"].corr(merged["ret"]))

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
# 9. NORMALIZE
# =========================
def normalize_score(df):
    if len(df) == 0:
        return df
    s_min = df["score"].min()
    s_max = df["score"].max()
    if s_max - s_min > 0:
        df["score_norm"] = ((df["score"] - s_min) / (s_max - s_min) * 100).round(2)
    else:
        df["score_norm"] = 50.0
    return df


# =========================
# MAIN ENGINE
# =========================
def run_engine():
    print(f"[ENGINE START] {ENGINE_VERSION}")

    biz_day = get_last_business_day()
    print(f"[DATE] 기준일: {biz_day}")

    # 1. 유니버스
    tickers = fetch_universe(biz_day)
    if not tickers:
        print("[FATAL] 유니버스 수집 실패")
        return

    # 2. 가격
    df = fetch_price(biz_day, tickers)
    if df.empty:
        print("[FATAL] 가격 데이터 없음")
        return

    # 3. 수급
    flow_df = fetch_flow(biz_day)
    if len(flow_df) > 0:
        df = df.merge(flow_df, on="code", how="left")
    else:
        df["foreign_net"] = 0
        df["inst_net"]    = 0

    # 4. DART
    dart_df = fetch_dart_score(biz_day)
    if len(dart_df) > 0:
        df = df.merge(dart_df, on="code", how="left")
    else:
        df["dart_score"] = 0

    # 5. 히스토리 로드 (momentum용)
    hist_prev = pd.read_csv(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None

    # 6. 팩터 계산
    df = compute_momentum(df, hist_prev)
    df = compute_score(df)

    # 7. 히스토리 저장
    hist = update_history(df, biz_day)

    # 8. IC
    ic = compute_ic_series(hist, window=IC_WINDOW)

    # 9. TOP N
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
        "version":   ENGINE_VERSION,
        "biz_day":   biz_day,
        "ic":        None if (ic is None or (isinstance(ic, float) and np.isnan(ic))) else ic,
        "ic_window": IC_WINDOW,
        "count":     int(len(df)),
        "top10":     records
    }

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[TOP1] {records[0].get('name', records[0]['code'])} / score {records[0]['score']}")
    print(f"[ENGINE DONE] {ENGINE_VERSION}")


if __name__ == "__main__":
    run_engine()
