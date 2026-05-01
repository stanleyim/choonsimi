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
FLOW_WEIGHTS = [0.5, 0.3, 0.2]

VOL_WINDOW          = 5
TOP_N               = 10
MIN_VALID_N         = 30
EPS                 = 1e-6
TURNOVER_THRESHOLD  = 50e8

ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE    = os.path.join(ROOT, "data.json")
HISTORY_FILE = os.path.join(ROOT, "history.csv")
RESULT_FILE  = os.path.join(ROOT, "result.json")


# =========================
# UTIL
# =========================
def zscore(s: pd.Series) -> pd.Series:
    """표준화(Z-score). 분산 0 또는 NaN이면 0 반환."""
    sd = s.std()
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / (sd + EPS)


# =========================
# HISTORY
# =========================
def update_history(df: pd.DataFrame) -> pd.DataFrame:
    """
    오늘 종가를 history.csv에 추가한다.
    - 동일 (code, date) 중복 제거
    - 저장 후 전체 history 반환
    """
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
    """
    외국인 + 기관 순매수를 기반으로 flow_z 산출.
    - 컬럼 미존재 시 0으로 초기화 (AttributeError 방지)
    - rolling은 code별 groupby transform으로 종목 혼합 방지
    """
    if "foreign_net" not in df.columns:
        df["foreign_net"] = 0.0
    else:
        df["foreign_net"] = df["foreign_net"].fillna(0.0)

    if "inst_net" not in df.columns:
        df["inst_net"] = 0.0
    else:
        df["inst_net"] = df["inst_net"].fillna(0.0)

    df["flow_raw"] = df["foreign_net"] + df["inst_net"]

    signals = []
    for w, wt in zip(FLOW_WINDOWS, FLOW_WEIGHTS):
        # groupby transform → 종목별 독립 rolling
        ma    = df.groupby("code")["flow_raw"].transform(
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
    """
    history 기반 다기간 모멘텀 산출.
    - hist 부족 시 mom_z = 0
    - NaN은 np.nan_to_num으로 안전하게 0 처리
    """
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
            0.5 * np.nan_to_num(r["ret_1"],  nan=0.0) +
            0.3 * np.nan_to_num(r["ret_5"],  nan=0.0) +
            0.2 * np.nan_to_num(r["ret_10"], nan=0.0)
        )

    df["mom_raw"] = df["code"].map(mom_map).fillna(0.0)
    df["mom_z"]   = zscore(df["mom_raw"])
    return df


# =========================
# DART
# =========================
def compute_dart(df: pd.DataFrame) -> pd.DataFrame:
    """
    DART 공시 점수 기반 dart_z 산출.
    - 컬럼 미존재 시 0으로 초기화
    """
    if "dart_score" not in df.columns:
        df["dart_score"] = 0.0
    else:
        df["dart_score"] = df["dart_score"].fillna(0.0)

    df["dart_ma3"]   = df.groupby("code")["dart_score"].transform(
                           lambda x: x.rolling(3, min_periods=1).mean()
                       )
    df["dart_delta"] = df["dart_score"] - df["dart_ma3"]
    df["dart_z"]     = zscore(df["dart_delta"])
    return df


# =========================
# NEXT RETURN  ★핵심 수정★
# =========================
def compute_next_return(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    """
    [버그 수정] 단일 스냅샷 df에 shift(-1) 적용 시 전부 NaN 문제 해결.
    history에서 종목별 마지막 확정 다음날 수익률을 역산한다.

    - hist 행 수 부족 시 next_return = NaN (IC 계산 skip)
    """
    if hist is None or len(hist) < 10:
        df["next_return"] = np.nan
        return df

    h = hist.sort_values(["code", "date"]).copy()
    # 각 날짜의 '다음 날' 수익률 = 다음 행의 일간 수익률
    h["next_return"] = h.groupby("code")["close"].pct_change().shift(-1)

    # 종목별 가장 최근 next_return 매핑
    nr_map = h.groupby("code")["next_return"].last()
    df["next_return"] = df["code"].map(nr_map)
    return df


# =========================
# IC
# =========================
def compute_ic(df: pd.DataFrame):
    """
    factor ~ next_return 상관계수(IC) 산출.
    유효 행 부족 또는 상관계수 NaN이면 None 반환.
    """
    valid = df.dropna(subset=["flow_z", "mom_z", "dart_z", "next_return"])

    if len(valid) < MIN_VALID_N:
        return None

    ic_flow = valid["flow_z"].corr(valid["next_return"])
    ic_mom  = valid["mom_z"].corr(valid["next_return"])
    ic_dart = valid["dart_z"].corr(valid["next_return"])

    return (ic_flow, ic_mom, ic_dart)


# =========================
# SCORE
# =========================
def compute_score(df: pd.DataFrame, w: dict) -> pd.DataFrame:
    """IC 가중치 기반 종합 점수 산출."""
    df["score"] = (
        w.get("flow_z", 0.0) * df["flow_z"] +
        w.get("mom_z",  0.0) * df["mom_z"]  +
        w.get("dart_z", 0.0) * df["dart_z"]
    )
    return df


# =========================
# FILTER  ★수정★
# =========================
def apply_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    상위 40% 종목만 통과.
    - fallback: 필터 후 TOP_N*3 미만이면 원본 df에서 상위 TOP_N*3 추출
    - reset_index: 이후 apply_vol의 인덱스 alignment 오류 방지
    """
    thr      = df["score"].quantile(0.6)
    filtered = df[df["score"] > thr]

    if len(filtered) < TOP_N * 3:
        return (df.sort_values("score", ascending=False)
                  .head(TOP_N * 3)
                  .reset_index(drop=True))

    return filtered.reset_index(drop=True)


# =========================
# VOL ADJUST  ★수정★
# =========================
def apply_vol(df: pd.DataFrame) -> pd.DataFrame:
    """
    변동성 역가중으로 adj_score 산출 → 비중 계산.
    - groupby + transform으로 종목별 독립 rolling std
    - reset_index 이후 인덱스가 연속이므로 alignment 안전
    """
    ret = df.groupby("code")["close"].transform(
              lambda x: x.pct_change()
          )
    vol = (ret.groupby(df["code"])
              .transform(lambda x: x.rolling(VOL_WINDOW, min_periods=1).std())
              .fillna(EPS))

    df["adj_score"] = df["score"] / (vol + EPS)

    total = df["adj_score"].sum()
    if total == 0 or np.isnan(total):
        df["weight"] = 1.0 / len(df)
    else:
        df["weight"] = df["adj_score"] / total

    return df


# =========================
# SAVE RESULT  ★수정★
# =========================
def save_result(df: pd.DataFrame, add: set = None, rem: set = None) -> None:
    """
    상위 TOP_N 종목을 result.json으로 저장.
    - .copy()로 SettingWithCopyWarning 방지
    - 편입(add) / 편출(rem) 종목 변경 내역 포함
    - score를 0~100으로 정규화
    """
    cols = ["code", "close", "score", "weight", "flow_z", "mom_z", "dart_z"]
    top  = (df.sort_values("score", ascending=False)
              .head(TOP_N)[cols]
              .copy())

    smin, smax = top["score"].min(), top["score"].max()
    if smax != smin:
        top["score"] = ((top["score"] - smin) / (smax - smin) * 100).round(2)
    else:
        top["score"] = 50.0

    result = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "top10": top.to_dict("records"),
        "changes": {
            "add":    sorted(list(add or [])),
            "remove": sorted(list(rem or []))
        }
    }

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  [RESULT] 저장 완료 → {RESULT_FILE}")
    print(f"  [CHANGE] 편입: {sorted(list(add or []))}  /  편출: {sorted(list(rem or []))}")


# =========================
# ENGINE
# =========================
def run_engine(df: pd.DataFrame) -> None:
    print("[ENGINE START]")

    # ── 기초 필터 ──────────────────────────────────
    df = df[df["close"].notna()].copy()
    df = df[df["volume"] > 0]

    df["code"]     = df["code"].astype(str).str.zfill(6)
    df["turnover"] = df["close"] * df["volume"]
    df = df[df["turnover"] > TURNOVER_THRESHOLD]

    if len(df) < 50:
        print("  [WARN] 거래대금 필터 후 종목 부족 → 상위 200개로 확장")
        df = df.sort_values("turnover", ascending=False).head(200)

    df = df.reset_index(drop=True)

    # ── 외국인/기관 순매수 ─────────────────────────
    codes    = df["code"].tolist()
    flow_map = build_flow_data(codes)

    df["foreign_net"] = df["code"].map(
        lambda x: flow_map.get(x, {}).get("foreign_net", 0)
    )
    df["inst_net"] = df["code"].map(
        lambda x: flow_map.get(x, {}).get("inst_net", 0)
    )

    # ── History 업데이트 ───────────────────────────
    hist = update_history(df)

    # ── Factor 계산 ────────────────────────────────
    df = compute_flow(df)
    df = compute_momentum(df, hist)
    df = compute_dart(df)
    df = compute_next_return(df, hist)   # ★ hist 인자 추가

    # ── IC → 가중치 ────────────────────────────────
    ic = compute_ic(df)

    if ic is None or any(np.isnan(v) for v in ic):
        print("  [IC] 유효 샘플 부족 또는 NaN → 디폴트 가중치 사용")
        w = {"flow_z": 0.6, "mom_z": 0.0, "dart_z": 0.4}
    else:
        print(f"  [IC] flow={ic[0]:.4f}  mom={ic[1]:.4f}  dart={ic[2]:.4f}")
        update_ic(*ic)
        w = compute_weights()

    print(f"  [WEIGHT] {w}")

    # ── 스코어링 → 필터 → 변동성 조정 ────────────
    df = compute_score(df, w)
    df = apply_filter(df)
    df = apply_vol(df)

    # ── 포트폴리오 관리 ────────────────────────────
    new_port     = build_portfolio(df, TOP_N)
    prev_port    = load_prev_portfolio()
    add, rem     = compare_portfolio(prev_port, new_port)
    save_portfolio(new_port)

    # ── 결과 저장 ──────────────────────────────────
    save_result(df, add, rem)

    print("[ENGINE DONE]")


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"data.json 없음: {DATA_FILE}")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if "all" not in raw or not raw["all"]:
        raise ValueError("data.json에 'all' 키가 없거나 비어있습니다.")

    df = pd.DataFrame(raw["all"])
    run_engine(df)
