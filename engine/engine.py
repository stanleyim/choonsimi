"""
engine.py — v7.0.0 CHOONSIMI CORE
─────────────────────────────────────────────────────────
목적  : 오늘 상승 진입 시초 종목 선정
기준  : 장마감 자료만 사용
        장중 수동실행 → 전일 장마감 자료 자동 fallback
        장마감 후 실행 → 당일 장마감 자료
특징  : 레짐별 파라미터 자동 조절 (UPTREND / DOWNTREND / SIDEWAY)
데이터: close, volume, change_rate (1일치) + 수급 + 뉴스 + 재무
메모  : 자료 축적 시 이동평균·캔들·RSI 파라미터 확장 예정
─────────────────────────────────────────────────────────
v7.0.0 vs v6.5.2 변경점
  ✔ 목적 재정의: 상승 진입 시초 종목 선정
  ✔ 레짐별 파라미터 REGIME_PARAMS 테이블로 일원화
  ✔ 모멘텀 스코어: sweet-spot 포물선 방식 (과열 제외)
  ✔ 거래량: 오늘 유니버스 내 백분위 순위 (절대값 아님)
  ✔ volume=0 방어: KIS 실시간 주입 후에도 0이면 필터 탈락
  ✔ load_stock_data: 장마감 여부 자동 판단 + 최신 거래일 fallback
  ✔ result.json: data_date / run_at / params_used 필드 추가
─────────────────────────────────────────────────────────
"""

import os, json, math, time
import pandas as pd
import requests
import holidays
from datetime import datetime, timezone, timedelta

# ── 경로 (engine/engine.py → repo root) ─────────────────
BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_HISTORY = os.path.join(BASE_DIR, "signal_history.csv")
HISTORY_CSV    = os.path.join(BASE_DIR, "history.csv")
RESULT_FILE    = os.path.join(BASE_DIR, "result.json")
FUND_FILE      = os.path.join(BASE_DIR, "fundamental.json")
FLOW_FILE      = os.path.join(BASE_DIR, "market_flow.json")
NEWS_FILE      = os.path.join(BASE_DIR, "news_scores.json")
FAILURE_FILE   = os.path.join(BASE_DIR, "failure_analysis.json")
FAILURE_HISTORY = os.path.join(BASE_DIR, "failure_history.csv")
TOKEN_FILE     = os.path.join(BASE_DIR, "kis_token.json")

# ── 상수 ────────────────────────────────────────────────
KST          = timezone(timedelta(hours=9))
KR_HOLIDAYS  = holidays.KR(years=[2025, 2026, 2027])
MARKET_CLOSE = 15 * 60 + 30   # 15:30 KST (분 단위)
MAX_GAP_DAYS = 7
KIS_BASE     = "https://openapi.koreainvestment.com:9443"
TIMEOUT      = 10
MAX_RETRY    = 3
DELAY        = 0.2
TOP_N        = 20
TOP_CORE     = 5
ENTRY_N      = 5

BLOCK_KW = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
    "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
    "레버리지","인버스","선물","REIT","리츠","INDEX","지수"
]

# ══════════════════════════════════════════════════════════
# 레짐별 파라미터 테이블
# 자료 축적 후 수치 정밀 튜닝 예정
# ══════════════════════════════════════════════════════════
REGIME_PARAMS = {
    "UPTREND": {
        # ── 사전 필터 ──
        "min_volume"   : 30_000,   # 상승장: 참여 종목 많아 기준 상향
        "min_price"    : 1_000,
        "vol_pct_min"  : 0.40,     # 거래량 하위 40% 제외

        # ── 모멘텀 sweet-spot (change_rate %) ──
        "chg_min"      : 0.3,      # 너무 낮으면 아직 시작 안 됨
        "chg_max"      : 7.0,      # 너무 높으면 이미 과열

        # ── 팩터 가중치 (합계 1.0) ──
        "W_MOM"        : 0.35,     # 상승장: 모멘텀 중심
        "W_FLOW"       : 0.30,
        "W_VOL"        : 0.20,
        "W_NEWS"       : 0.10,
        "W_FUND"       : 0.05,

        "regime_bonus" : 0.05,
    },
    "DOWNTREND": {
        # 하락장: 강한 종목만 선별 → 기준 대폭 강화
        "min_volume"   : 50_000,
        "min_price"    : 2_000,
        "vol_pct_min"  : 0.65,     # 거래량 상위 35%만

        "chg_min"      : 0.0,      # 하락장에서 플러스 = 이미 강함
        "chg_max"      : 5.0,

        "W_MOM"        : 0.20,
        "W_FLOW"       : 0.40,     # 하락장: 수급(기관/외국인 방어) 핵심
        "W_VOL"        : 0.10,
        "W_NEWS"       : 0.05,
        "W_FUND"       : 0.25,     # 재무 탄탄한 방어주 중심

        "regime_bonus" : -0.05,
    },
    "SIDEWAY": {
        # 횡보장: 거래량 급증이 돌파 시초 핵심 신호
        "min_volume"   : 20_000,
        "min_price"    : 1_000,
        "vol_pct_min"  : 0.50,     # 거래량 상위 50%

        "chg_min"      : 0.3,
        "chg_max"      : 6.0,

        "W_MOM"        : 0.25,
        "W_FLOW"       : 0.30,
        "W_VOL"        : 0.30,     # 횡보장: 거래량 급증이 핵심
        "W_NEWS"       : 0.10,
        "W_FUND"       : 0.05,

        "regime_bonus" : 0.0,
    },
}


# ══════════════════════════════════════════════════════════
# UTILS
# ══════════════════════════════════════════════════════════
def safe_float(v, d=0.0):
    try:    return float(str(v).replace(",", ""))
    except: return d

def safe_int(v, d=0):
    try:    return int(str(v).replace(",", ""))
    except: return d

def tanh_norm(v):
    return (math.tanh(v) + 1) / 2

def is_common_stock(code, name=""):
    code = str(code).strip()
    name = str(name or "").strip().upper()
    if not code.isdigit() or len(code) != 6: return False
    if code[-1] in ("5", "7", "9"):          return False
    if name in ("", "NAN", "NONE"):           return False
    return not any(k in name for k in BLOCK_KW)

def get_next_trading_day(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date() + timedelta(days=1)
    for _ in range(MAX_GAP_DAYS):
        if d.weekday() < 5 and d not in KR_HOLIDAYS:
            return d.strftime("%Y-%m-%d")
        d += timedelta(days=1)
    return None

def is_market_closed():
    """현재 KST 기준 장마감 여부 (15:30 이후 = 마감)"""
    now = datetime.now(KST)
    return (now.hour * 60 + now.minute) >= MARKET_CLOSE

def load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except: return {}


# ══════════════════════════════════════════════════════════
# KIS TOKEN / PRICE
# ══════════════════════════════════════════════════════════
def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        issued_str = data.get("issued_at", "").replace("Z", "") or "2000-01-01T00:00:00"
        issued = datetime.fromisoformat(issued_str)
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=KST)
        if (datetime.now(KST) - issued).total_seconds() < 21600:
            return data.get("access_token")
    except: pass

    for _ in range(MAX_RETRY):
        try:
            r = requests.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     os.environ.get("KIS_APP_KEY", ""),
                    "appsecret":  os.environ.get("KIS_APP_SECRET", "")
                },
                timeout=TIMEOUT
            )
            r.raise_for_status()
            token = r.json().get("access_token")
            with open(TOKEN_FILE, "w", encoding="utf-8-sig") as f:
                json.dump({"access_token": token,
                           "issued_at": datetime.now(KST).isoformat()}, f)
            return token
        except: time.sleep(1)
    return None

def kis_headers(token, tr_id):
    return {
        "authorization": f"Bearer {token}",
        "appkey":        os.environ.get("KIS_APP_KEY", ""),
        "appsecret":     os.environ.get("KIS_APP_SECRET", ""),
        "tr_id":         tr_id,
        "content-type":  "application/json",
        "custtype":      "P"
    }

def fetch_price_kis(token, code):
    if not token: return {}
    for _ in range(MAX_RETRY):
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=kis_headers(token, "FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=TIMEOUT
            )
            if r.status_code == 401: return {}
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0": return {}
            o = d.get("output") or d.get("output1") or {}
            if isinstance(o, list): o = o[0] if o else {}
            return {
                "close"      : safe_int(o.get("stck_prpr")),
                "volume"     : safe_int(o.get("acml_vol")),
                "change_rate": safe_float(o.get("prdy_ctrt")),
            }
        except: time.sleep(DELAY)
    return {}

def enrich_with_kis(stocks, token):
    """volume=0 또는 change_rate=0 종목을 KIS 실시간으로 보완"""
    if not token: return stocks
    enriched, patched = [], 0
    for s in stocks:
        needs_patch = (
            safe_float(s.get("volume"))      == 0 or
            safe_float(s.get("change_rate")) == 0
        )
        if needs_patch:
            code = str(s.get("code", "")).zfill(6)
            p    = fetch_price_kis(token, code)
            if p and p.get("close", 0) > 0:
                s = {**s, **p}
                patched += 1
            time.sleep(DELAY)
        enriched.append(s)
    print(f"[KIS] 보완 완료: {patched}종목 패치 / {len(enriched)}종목 전체")
    return enriched


# ══════════════════════════════════════════════════════════
# DATA LOAD — 장마감 여부 자동 판단 + fallback
# ══════════════════════════════════════════════════════════
def load_stock_data(today):
    try:
        df = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["code"]     = df["code"].str.zfill(6)
        df["date"]     = pd.to_datetime(df["date"], errors="coerce")
        df             = df.dropna(subset=["date"])
        df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")

        # 장마감(15:30) 이후 + 오늘 데이터 있으면 → 오늘 사용
        # 그 외 → 최신 거래일 자동 fallback
        if today in df["date_str"].values and is_market_closed():
            target_date = today
            source      = "오늘 장마감"
        else:
            target_date = df["date_str"].max()
            source      = "전일 장마감 (fallback)" if target_date != today else "오늘 장마감"

        latest = df[df["date_str"] == target_date].copy()
        print(f"[DATA] {source} | date={target_date} | rows={len(latest)}")
        return latest.to_dict("records"), target_date

    except Exception as e:
        print(f"[DATA ERROR] {e}")
        return [], today


# ══════════════════════════════════════════════════════════
# REGIME
# ══════════════════════════════════════════════════════════
def compute_regime():
    flow   = load_json(FLOW_FILE)
    segs   = ["KOSPI_foreign","KOSPI_institution","KOSDAQ_foreign","KOSDAQ_institution"]
    scores = [flow.get(s, {}).get("score", 0) for s in segs]
    valid  = [s for s in scores if s != 0]
    fs     = max(-1.0, min(1.0, sum(valid) / len(valid) if valid else 0))

    if   fs >  0.3: return "UPTREND",   round(abs(fs), 2)
    elif fs < -0.3: return "DOWNTREND",  round(abs(fs), 2)
    else:           return "SIDEWAY",    0.50


# ══════════════════════════════════════════════════════════
# FLOW MAP
# ══════════════════════════════════════════════════════════
def build_flow_map(flow):
    fm = {}
    for seg, w in [
        ("KOSPI_foreign",      0.36),
        ("KOSPI_institution",  0.24),
        ("KOSDAQ_foreign",     0.24),
        ("KOSDAQ_institution", 0.16),
    ]:
        for r in flow.get(seg, {}).get("rows", []):
            c = str(r.get("code", "")).zfill(6)
            fm[c] = fm.get(c, 0) + safe_float(r.get("net")) * w
    return fm


# ══════════════════════════════════════════════════════════
# PRE-FILTER (레짐별 기준)
# ══════════════════════════════════════════════════════════
def pre_filter(stocks, params):
    min_vol   = params["min_volume"]
    min_price = params["min_price"]

    filtered = [
        s for s in stocks
        if safe_float(s.get("volume"))  > 0
        and safe_float(s.get("volume")) >= min_vol
        and safe_float(s.get("close"))  >= min_price
        and is_common_stock(s.get("code",""), s.get("name",""))
    ]

    # 폴백: 기준 50% 완화
    if len(filtered) < 10:
        filtered = [
            s for s in stocks
            if safe_float(s.get("volume"))  > 0
            and safe_float(s.get("volume")) >= min_vol   * 0.5
            and safe_float(s.get("close"))  >= min_price * 0.5
            and is_common_stock(s.get("code",""), s.get("name",""))
        ]
        print(f"[FILTER] 폴백 적용 → {len(filtered)}종목")

    return filtered


# ══════════════════════════════════════════════════════════
# 거래량 백분위 (오늘 유니버스 내 상대 순위 0~1)
# 절대값이 아닌 상대 순위 → 거래 많은 날/적은 날 모두 공평
# ══════════════════════════════════════════════════════════
def compute_vol_percentile(stocks):
    pairs = [
        (str(s.get("code","")).zfill(6), safe_float(s.get("volume")))
        for s in stocks
    ]
    pairs_sorted = sorted(pairs, key=lambda x: x[1])
    n = len(pairs_sorted)
    return {code: i / max(n - 1, 1) for i, (code, _) in enumerate(pairs_sorted)}


# ══════════════════════════════════════════════════════════
# 핵심 스코어링 — 상승 진입 시초
#
# ① 모멘텀 (포물선):
#    change_rate sweet-spot 중간값 → 최대점
#    너무 낮으면 시작 안 됨 / 너무 높으면 이미 과열
#
# ② 거래량 백분위:
#    유니버스 내 상대 순위 (0~1)
#    급증 = 진입 시초 신호
#
# ③ 수급 (외국인+기관):
#    방향성 확인
#
# ④ 재무:
#    ROE 높고 부채비율 낮을수록 가중
#
# ⑤ 뉴스:
#    긍정 뉴스 보조 신호
# ══════════════════════════════════════════════════════════
def score_entry_signal(s, flow_map, flow_max, vol_pct, fund, news, params):
    code = str(s.get("code", "")).zfill(6)
    chg  = safe_float(s.get("change_rate"))
    fd   = fund.get(code, {})
    nv   = safe_float(news.get(code, 0))

    # ── ① 모멘텀 (포물선) ─────────────────────────────────
    chg_min   = params["chg_min"]
    chg_max   = params["chg_max"]
    chg_mid   = (chg_min + chg_max) / 2
    half      = (chg_max - chg_min) / 2

    if chg < chg_min or chg > chg_max:
        mom = 0.0
    else:
        mom = max(0.0, 1.0 - abs(chg - chg_mid) / half)

    # ── ② 거래량 백분위 ───────────────────────────────────
    vol_sc = vol_pct.get(code, 0.0)

    # ── ③ 수급 ────────────────────────────────────────────
    flow_val = flow_map.get(code, 0)
    flow_sc  = tanh_norm(flow_val / (flow_max or 1) * 2.5)

    # ── ④ 재무 ────────────────────────────────────────────
    if fd:
        roe        = safe_float(fd.get("roe",        0))
        debt_ratio = safe_float(fd.get("debt_ratio", 200))
        fund_sc    = tanh_norm(roe / 15) * max(0.0, 1.0 - min(debt_ratio, 300) / 300)
    else:
        fund_sc = 0.5

    # ── ⑤ 뉴스 ────────────────────────────────────────────
    news_sc = tanh_norm(nv)

    # ── 가중 합산 + 레짐 보정 ─────────────────────────────
    raw = (
        mom     * params["W_MOM"]  +
        flow_sc * params["W_FLOW"] +
        vol_sc  * params["W_VOL"]  +
        news_sc * params["W_NEWS"] +
        fund_sc * params["W_FUND"]
    ) + params["regime_bonus"]

    return round(max(0.0, min(100.0, raw * 100)), 2)


# ══════════════════════════════════════════════════════════
# TOP20 선정
# ══════════════════════════════════════════════════════════
def select_top20(stocks, flow_map, vol_pct, fund, news, params):
    flow_vals = sorted([abs(v) for v in flow_map.values()])
    flow_max  = flow_vals[int(len(flow_vals) * 0.95) - 1] if flow_vals else 1.0
    vol_pct_min = params["vol_pct_min"]

    scored = []
    for s in stocks:
        code = str(s.get("code","")).zfill(6)
        # 거래량 백분위 미달 → 진입 시초 조건 불충족
        if vol_pct.get(code, 0) < vol_pct_min:
            continue
        sc = score_entry_signal(s, flow_map, flow_max, vol_pct, fund, news, params)
        if sc > 0:
            scored.append((sc, s))

    scored.sort(reverse=True, key=lambda x: x[0])

    result = []
    for i, (sc, s) in enumerate(scored[:TOP_N], 1):
        code = str(s.get("code","")).zfill(6)
        fd   = fund.get(code, {})
        chg  = safe_float(s.get("change_rate"))
        # 수급·모멘텀 방향 일치 시 기대수익 가중
        fq   = 1.0 if flow_map.get(code, 0) * chg > 0 else 0.8
        exp  = round((sc - 50) * 0.06 * fq, 2)

        result.append({
            "rank"              : i,
            "code"              : code,
            "name"              : s.get("name", ""),
            "score"             : sc,
            "price"             : int(safe_float(s.get("close"))),
            "change_pct"        : chg,
            "expected_return_5d": exp,
            "roe"               : fd.get("roe"),
            "debt_ratio"        : fd.get("debt_ratio"),
            "volume"            : int(safe_float(s.get("volume"))),
            "vol_percentile"    : round(vol_pct.get(code, 0), 3),
        })
    return result


# ══════════════════════════════════════════════════════════
# ENTRY SIGNAL — TOP20 중 가장 강한 진입 신호
# 수급 양수 + 모멘텀 sweet-spot + 거래량 상위 동시 충족
# ══════════════════════════════════════════════════════════
def build_entry_top5(top20, flow_map, params):
    chg_min     = params["chg_min"]
    chg_max     = params["chg_max"]
    vol_pct_min = params["vol_pct_min"]

    candidates = []
    for t in top20:
        code = t["code"]
        chg  = t["change_pct"]
        vp   = t["vol_percentile"]
        flow = flow_map.get(code, 0)

        if flow <= 0:                        continue  # 수급 양수 필수
        if not (chg_min <= chg <= chg_max):  continue  # 모멘텀 sweet-spot
        if vp < vol_pct_min:                 continue  # 거래량 조건

        candidates.append(t)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    result = []
    for i, c in enumerate(candidates[:ENTRY_N], 1):
        result.append({**c, "rank": i, "entry_score": c["score"]})
    return result


# ══════════════════════════════════════════════════════════
# VERIFY — 신호 성과 검증 (1일/5일/20일/전체)
#
# window_days=1     → 어제 1일치만
# window_days=5     → 최근 5거래일
# window_days=20    → 최근 20거래일
# window_days=None  → 전체 누적
# ══════════════════════════════════════════════════════════
def verify_window(today, window_days=None, label=""):
    """
    signal_history.csv 의 신호들을
    history.csv 종가와 비교해 성과 계산.
    
    각 신호일 신호 → 다음 거래일 종가 비교 (1일 수익률 누적)
    """
    try:
        hist = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        hist["code"]  = hist["code"].str.zfill(6)
        hist["date"]  = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        hist["close"] = pd.to_numeric(hist["close"], errors="coerce")

        sig        = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        sig["code"] = sig["code"].str.zfill(6)
        prev_dates = sorted([d for d in sig["date"].dropna().unique() if d < today])

        if not prev_dates:
            return {"win_rate": 0, "avg_return": 0, "top5_return": 0, "sample": 0, "days": 0}

        # 윈도우 적용 (최근 N 거래일)
        if window_days is not None:
            prev_dates = prev_dates[-window_days:]

        all_returns  = []
        top5_returns = []
        hits         = 0
        total        = 0

        for sig_date in prev_dates:
            eval_day = get_next_trading_day(sig_date)
            if not eval_day or eval_day > today:
                continue

            hist_eval = hist[hist["date"] == eval_day]
            if hist_eval.empty:
                continue

            price_map = {
                k: v for k, v in zip(hist_eval["code"], hist_eval["close"])
                if pd.notna(v) and float(v) > 0
            }

            sig_y = sig[sig["date"] == sig_date]

            for _, r in sig_y.iterrows():
                code  = str(r["code"]).zfill(6)
                entry = safe_float(r.get("price"))
                exitp = price_map.get(code, 0)
                if entry > 0 and exitp > 0:
                    ret = (exitp - entry) / entry * 100
                    all_returns.append(ret)
                    total += 1
                    if ret > 0:
                        hits += 1
                    if safe_float(r.get("rank", 999)) <= 5:
                        top5_returns.append(ret)

        result = {
            "win_rate"   : round(hits / total * 100, 1)              if total          else 0,
            "avg_return" : round(sum(all_returns)  / len(all_returns), 2) if all_returns  else 0,
            "top5_return": round(sum(top5_returns) / len(top5_returns), 2) if top5_returns else 0,
            "sample"     : total,
            "days"       : len(prev_dates),
        }

        if label:
            print(f"[VERIFY-{label}] 신호일 {len(prev_dates)}일 | "
                  f"표본 {total}건 | win {result['win_rate']}% | "
                  f"avg {result['avg_return']:+.2f}% | top5 {result['top5_return']:+.2f}%")

        return result

    except FileNotFoundError:
        print("[VERIFY] signal_history.csv 없음 — 데이터 축적 시작")
        return {"win_rate": 0, "avg_return": 0, "top5_return": 0, "sample": 0, "days": 0}
    except Exception as e:
        print(f"[VERIFY ERROR] {e}")
        return {"win_rate": 0, "avg_return": 0, "top5_return": 0, "sample": 0, "days": 0}


# 기존 verify() 호출은 1일치 윈도우 호출과 동일
def verify(today):
    return verify_window(today, window_days=1, label="1D")


# ══════════════════════════════════════════════════════════
# SIGNAL HISTORY 저장
# ══════════════════════════════════════════════════════════
def save_signal_history(top20, regime, data_date):
    df = pd.DataFrame([{
        "date"          : data_date,
        "regime"        : regime,
        "rank"          : t["rank"],
        "code"          : str(t["code"]).zfill(6),
        "name"          : t["name"],
        "score"         : t["score"],
        "price"         : t["price"],
        "change_pct"    : t["change_pct"],
        "vol_percentile": t.get("vol_percentile", 0),
    } for t in top20])

    try:
        old = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        old["code"] = old["code"].str.zfill(6)
        if "date" in old.columns:
            old = old[old["date"] != data_date]
        df = pd.concat([old, df], ignore_index=True)
    except: pass

    df.to_csv(SIGNAL_HISTORY, index=False, encoding="utf-8-sig")
    print(f"[SIGNAL] 저장: {len(df)} rows")


# ══════════════════════════════════════════════════════════
# FAILURE ANALYSIS — 안 오른 종목의 이유 분석 (다중 증거)
#
# window_days=1 → 어제 신호 → 오늘 평가 (1일 후)
# window_days=5 → 5일 전 신호 → 오늘 평가 (5일 후)
#
# 출력: failure_analysis.json (오늘분)
#       failure_history.csv (누적)
# ══════════════════════════════════════════════════════════
def analyze_failures(today, window_days=1, label="1D"):
    """
    안 오른 종목의 다중 증거 수집:
      ① 거래량 변화 (history.csv)
      ② 수급 변화  (market_flow.json)
      ③ 과열 후 조정 (어제 change_pct + 오늘 결과)
      ④ 시가-종가 패턴 (OHLC)
      ⑤ 뉴스 점수 (news_scores.json)
    """
    try:
        # ── 데이터 로드 ───────────────────────────────────
        hist = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        hist["code"]  = hist["code"].str.zfill(6)
        hist["date"]  = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        hist["close"] = pd.to_numeric(hist["close"], errors="coerce")

        sig = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        sig["code"] = sig["code"].str.zfill(6)

        flow = load_json(FLOW_FILE)
        news_raw = load_json(NEWS_FILE)
        news_map = news_raw.get("scores", {}) if isinstance(news_raw, dict) else {}

        # ── 신호일 결정 (N일 전 거래일) ──────────────────
        prev_dates = sorted([d for d in sig["date"].dropna().unique() if d < today])
        if len(prev_dates) < window_days:
            return {
                "window": label,
                "status": "data_insufficient",
                "message": f"{window_days}일 전 신호 데이터 없음",
                "failures": []
            }

        sig_date = prev_dates[-window_days]
        eval_day = hist["date"].max()   # 가장 최근 거래일 = 평가일

        # ── 가격/거래량 매핑 ─────────────────────────────
        hist_sig  = hist[hist["date"] == sig_date]
        hist_eval = hist[hist["date"] == eval_day]

        if hist_eval.empty:
            return {
                "window": label,
                "status": "no_eval_data",
                "message": f"평가일({eval_day}) 가격 데이터 없음",
                "failures": []
            }

        price_map = {
            str(r["code"]).zfill(6): safe_float(r["close"])
            for _, r in hist_eval.iterrows() if pd.notna(r["close"]) and r["close"] > 0
        }
        vol_eval = {str(r["code"]).zfill(6): safe_float(r["volume"]) for _, r in hist_eval.iterrows()}
        vol_sig  = {str(r["code"]).zfill(6): safe_float(r["volume"]) for _, r in hist_sig.iterrows()}

        # OHLC (평가일)
        ohlc_map = {}
        for _, r in hist_eval.iterrows():
            ohlc_map[str(r["code"]).zfill(6)] = {
                "open":   safe_float(r.get("open")),
                "high":   safe_float(r.get("high")),
                "low":    safe_float(r.get("low")),
                "close":  safe_float(r.get("close")),
            }

        # 수급 - 평가일 market_flow.json 의 매수상위 종목 코드 모음
        flow_codes_today = set()
        for seg in ["KOSPI_foreign", "KOSPI_institution", "KOSDAQ_foreign", "KOSDAQ_institution"]:
            for r in flow.get(seg, {}).get("rows", []):
                flow_codes_today.add(str(r.get("code", "")).zfill(6))

        # ── 신호일 TOP20 추출 ────────────────────────────
        sig_y = sig[sig["date"] == sig_date].sort_values("rank")

        failures = []
        winners  = 0
        losers   = 0

        for _, r in sig_y.iterrows():
            code         = str(r["code"]).zfill(6)
            name         = r.get("name", "")
            rank         = int(r.get("rank", 99))
            entry_price  = safe_float(r.get("price"))
            sig_change   = safe_float(r.get("change_pct"))
            exp_return   = safe_float(r.get("expected_return_5d", 0))

            exit_price = price_map.get(code, 0)
            if entry_price <= 0 or exit_price <= 0:
                continue

            actual_return = round((exit_price - entry_price) / entry_price * 100, 2)

            # 승/패 카운트
            if actual_return > 0:
                winners += 1
            else:
                losers += 1

            # 실패 판정
            is_absolute_loss = actual_return < 0
            is_expected_miss = (exp_return > 0) and (actual_return < exp_return * 0.5)

            if not (is_absolute_loss or is_expected_miss):
                continue   # 충분히 성공 → 분석 대상 아님

            # ── 다중 증거 수집 ──────────────────────────
            evidence = []

            # ① 거래량 변화
            v_sig  = vol_sig.get(code, 0)
            v_eval = vol_eval.get(code, 0)
            if v_sig > 0 and v_eval > 0:
                vol_change = round((v_eval - v_sig) / v_sig * 100, 1)
                if vol_change <= -30:
                    evidence.append({
                        "factor": "volume_drop",
                        "signal_day_volume": int(v_sig),
                        "eval_day_volume":   int(v_eval),
                        "change_pct":        vol_change,
                        "interpretation":    f"거래량 급감 ({vol_change}%) — 관심 식음",
                        "impact":            "high"
                    })
                elif vol_change <= -10:
                    evidence.append({
                        "factor": "volume_drop",
                        "signal_day_volume": int(v_sig),
                        "eval_day_volume":   int(v_eval),
                        "change_pct":        vol_change,
                        "interpretation":    f"거래량 감소 ({vol_change}%)",
                        "impact":            "medium"
                    })

            # ② 수급 변화 (단순화 — 평가일 매수상위에서 빠졌는지)
            if code not in flow_codes_today:
                evidence.append({
                    "factor":         "flow_weakening",
                    "interpretation": "외국인/기관 매수상위 제외 — 수급 약화 추정",
                    "impact":         "high"
                })

            # ③ 과열 후 조정
            if sig_change >= 5 and actual_return < 0:
                evidence.append({
                    "factor":              "overheating",
                    "signal_day_change":   sig_change,
                    "eval_day_change":     actual_return,
                    "interpretation":      f"과열 후 조정 (어제 +{sig_change:.1f}% → 오늘 {actual_return:+.1f}%) — 차익실현 추정",
                    "impact":              "high"
                })

            # ④ 시가-종가 패턴
            ohlc = ohlc_map.get(code, {})
            if ohlc:
                o, h, l, c = ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"]
                if o > 0 and c > 0:
                    # 갭하락 시작
                    if o < entry_price * 0.99:
                        gap = round((o - entry_price) / entry_price * 100, 2)
                        evidence.append({
                            "factor":         "gap_down",
                            "open":           int(o),
                            "close":          int(c),
                            "gap_pct":        gap,
                            "interpretation": f"갭하락 시작 (시초가 {gap}%) — 매도 압력 우세",
                            "impact":         "high"
                        })
                    # 갭상승 후 종가 하락
                    elif o > entry_price * 1.005 and c < o:
                        intra_drop = round((c - o) / o * 100, 2)
                        evidence.append({
                            "factor":         "intraday_reversal",
                            "open":           int(o),
                            "high":           int(h),
                            "close":          int(c),
                            "intra_drop_pct": intra_drop,
                            "interpretation": f"갭상승 후 장중 하락 ({intra_drop}%) — 매도세 우세",
                            "impact":         "high"
                        })

            # ⑤ 뉴스 점수 (평가일 기준)
            news_score = safe_float(news_map.get(code, 0))
            if news_score < -0.2:
                evidence.append({
                    "factor":          "negative_news",
                    "today_score":     round(news_score, 2),
                    "interpretation":  "부정 뉴스 감지 (악재 키워드 노출)",
                    "impact":          "medium"
                })

            # ── 주된 이유 결정 ─────────────────────────
            high_impact = [e for e in evidence if e.get("impact") == "high"]
            main_reason = high_impact[0] if high_impact else (evidence[0] if evidence else {
                "factor":         "unknown",
                "interpretation": "특이 패턴 미감지 (외부 요인 또는 시장 전반 영향 추정)",
                "impact":         "low"
            })

            failures.append({
                "rank":               rank,
                "code":               code,
                "name":               name,
                "signal_day":         sig_date,
                "eval_day":           eval_day,
                "entry_price":        int(entry_price),
                "exit_price":         int(exit_price),
                "expected_return_5d": exp_return,
                "actual_return":      actual_return,
                "miss_by":            round(actual_return - exp_return, 2),
                "failure_type":       "absolute_loss" if is_absolute_loss else "expected_miss",
                "main_reason":        main_reason,
                "evidence":           evidence,
                "evidence_count":     len(evidence),
            })

        # ── 패턴 통계 ───────────────────────────────────
        pattern_counts = {}
        for f in failures:
            for e in f["evidence"]:
                factor = e.get("factor", "unknown")
                pattern_counts[factor] = pattern_counts.get(factor, 0) + 1

        most_common = (max(pattern_counts.items(), key=lambda x: x[1])[0]
                       if pattern_counts else None)

        result = {
            "window":              label,
            "signal_day":          sig_date,
            "eval_day":            eval_day,
            "total_signals":       len(sig_y),
            "winners":             winners,
            "losers":              losers,
            "failures":            failures,
            "pattern_stats":       pattern_counts,
            "most_common_failure": most_common,
        }

        print(f"[FAILURE-{label}] 신호일 {sig_date} → 평가일 {eval_day} | "
              f"win {winners} / loss {losers} | 분석 {len(failures)}건 | "
              f"주요 원인: {most_common or '없음'}")

        return result

    except FileNotFoundError:
        return {"window": label, "status": "file_not_found", "failures": []}
    except Exception as e:
        print(f"[FAILURE-{label} ERROR] {e}")
        return {"window": label, "status": "error", "error": str(e), "failures": []}


def save_failure_history(failure_1d, failure_5d, today):
    """
    failure_history.csv 에 누적 저장 (영구 보존)
    1년/2년 후 패턴 분석용
    """
    rows = []

    for src, label in [(failure_1d, "1D"), (failure_5d, "5D")]:
        for f in src.get("failures", []):
            main = f.get("main_reason") or {}
            rows.append({
                "date":                today,
                "window":              label,
                "signal_day":          f.get("signal_day", ""),
                "eval_day":            f.get("eval_day", ""),
                "rank":                f.get("rank", 0),
                "code":                f.get("code", ""),
                "name":                f.get("name", ""),
                "entry_price":         f.get("entry_price", 0),
                "exit_price":          f.get("exit_price", 0),
                "expected_return_5d":  f.get("expected_return_5d", 0),
                "actual_return":       f.get("actual_return", 0),
                "miss_by":             f.get("miss_by", 0),
                "failure_type":        f.get("failure_type", ""),
                "main_factor":         main.get("factor", ""),
                "main_interpretation": main.get("interpretation", ""),
                "evidence_count":      f.get("evidence_count", 0),
            })

    if not rows:
        print("[FAILURE-HISTORY] 저장할 실패 사례 없음")
        return

    new_df = pd.DataFrame(rows)
    try:
        old = pd.read_csv(FAILURE_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        # 같은 날짜+window 조합 중복 제거
        key_cols = ["date", "window", "code"]
        old = old[~old.set_index(key_cols).index.isin(new_df.set_index(key_cols).index)]
        df = pd.concat([old, new_df], ignore_index=True)
    except FileNotFoundError:
        df = new_df

    df.to_csv(FAILURE_HISTORY, index=False, encoding="utf-8-sig")
    print(f"[FAILURE-HISTORY] 누적 저장: {len(rows)}건 추가 (총 {len(df)}건)")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def run():
    today  = datetime.now(KST).strftime("%Y-%m-%d")
    run_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[START] engine v7.2.0  run_at={run_at}")

    # ① 데이터 로드 (장마감 여부 자동 판단)
    stocks, data_date = load_stock_data(today)
    if not stocks:
        print("[ERROR] 데이터 없음"); return

    # ② 보조 데이터
    flow     = load_json(FLOW_FILE)
    news     = load_json(NEWS_FILE).get("scores", {})
    fund_raw = load_json(FUND_FILE)
    fund_list = fund_raw if isinstance(fund_raw, list) else fund_raw.get("stocks", [])
    fund     = {str(s.get("code","")).zfill(6): s for s in fund_list}

    # ③ KIS 실시간 보완 (volume=0 방어)
    token = get_token()
    if token:
        stocks = enrich_with_kis(stocks, token)

    # ④ 레짐 판단
    regime, confidence = compute_regime()
    params = REGIME_PARAMS[regime]
    print(f"[REGIME] {regime}  confidence={confidence}")

    # ⑤ 사전 필터
    filtered = pre_filter(stocks, params)
    print(f"[UNIVERSE] {len(filtered)}종목 (전체 {len(stocks)}종목 중)")
    if not filtered:
        print("[WARN] 필터링 후 종목 없음"); return

    # ⑥ 거래량 백분위
    vol_pct  = compute_vol_percentile(filtered)

    # ⑦ 수급 맵
    flow_map = build_flow_map(flow)

    # ⑧ 상승 진입 시초 스코어링 + TOP20
    top20 = select_top20(filtered, flow_map, vol_pct, fund, news, params)
    print(f"[TOP20] {len(top20)}종목 선정")
    if not top20:
        print("[WARN] 스코어링 결과 없음"); return

    # ⑨ TOP5 CORE
    top5_core = [t for t in top20[:TOP_CORE] if t.get("expected_return_5d", 0) > 1.5]
    if not top5_core:
        top5_core = top20[:3]

    # ⑩ ENTRY SIGNAL
    entry_top5 = build_entry_top5(top20, flow_map, params)

    # ⑪ 성과 검증 (4개 윈도우: 1일/5일/20일/전체)
    perf_today = verify_window(today, window_days=1,    label="1D")
    perf_5d    = verify_window(today, window_days=5,    label="5D")
    perf_20d   = verify_window(today, window_days=20,   label="20D")
    perf_all   = verify_window(today, window_days=None, label="ALL")

    # ⑪-2 실패 분석 (안 오른 종목 다중 증거 수집)
    failure_1d = analyze_failures(today, window_days=1, label="1D")
    failure_5d = analyze_failures(today, window_days=5, label="5D")

    # ── 장마감 시간 체크 ──────────────────────────────────
    now_kst = datetime.now(KST)
    minutes_now = now_kst.hour * 60 + now_kst.minute
    is_after_close = minutes_now >= (15 * 60 + 30)   # 15:30 KST

    # ⑫ signal_history 저장 (장마감 후만)
    if is_after_close:
        save_signal_history(top20, regime, data_date)
    else:
        print("[SKIP] 장마감 이전 — signal_history 저장 스킵")

    # ⑬ result.json 저장 (장마감 후만)
    result = {
        "date"             : data_date,
        "run_at"           : run_at,
        "regime"           : regime,
        "confidence"       : confidence,
        "universe_size"    : len(filtered),
        "top20"            : top20,
        "top5_core"        : top5_core,
        "entry_top5"       : entry_top5,
        "performance_today": perf_today,
        "performance_5d"   : perf_5d,
        "performance_20d"  : perf_20d,
        "performance_all"  : perf_all,
        "params_used"      : {
            "regime"      : regime,
            "chg_min"     : params["chg_min"],
            "chg_max"     : params["chg_max"],
            "min_vol"     : params["min_volume"],
            "vol_pct_min" : params["vol_pct_min"],
        }
    }

    # ⑬-2 failure_analysis.json 데이터 구조
    failure_data = {
        "date":         data_date,
        "run_at":       run_at,
        "analysis_1d":  failure_1d,
        "analysis_5d":  failure_5d,
    }

    if is_after_close:
        with open(RESULT_FILE, "w", encoding="utf-8-sig") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # failure_analysis.json 저장
        with open(FAILURE_FILE, "w", encoding="utf-8-sig") as f:
            json.dump(failure_data, f, ensure_ascii=False, indent=2)

        # failure_history.csv 누적 저장
        save_failure_history(failure_1d, failure_5d, data_date)

        print(f"[DONE] regime={regime} | top20={len(top20)} | entry={len(entry_top5)} | "
              f"today={perf_today['win_rate']}% | all={perf_all['win_rate']}%(샘플 {perf_all['sample']})")
    else:
        print(f"[SKIP] 장마감 이전 ({now_kst.strftime('%H:%M')} KST) — "
              f"result.json / failure_analysis.json 저장 스킵 (기존 데이터 유지)")
        print(f"[TEST] 결과 미리보기: regime={regime} | top1={top20[0]['name']} ({top20[0]['score']})" 
              if top20 else "[TEST] 결과: 종목 없음")


if __name__ == "__main__":
    run()
