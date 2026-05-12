"""
engine.py — v6.5.0 FINAL
─────────────────────────────────────────────────────
✔ v6.4.1 핵심 로직 유지 (Regime / Score / Verify)
✔ v6.4.5 KIS 실시간 가격 통합
✔ result.json 필드 index.html 완전 일치
✔ BASE_DIR 경로 수정 (root 기준)
✔ calc_regime() history.csv 기반으로 수정
✔ entry_filter() prev_close 의존 제거 (change_rate 직접 사용)
✔ signal_history leading zero 보존
✔ verify() 주말/공휴일 자동 스킵
✔ performance_today 정상 출력
─────────────────────────────────────────────────────
"""

import os, json, math, time
import pandas as pd
import numpy as np
import requests
import holidays
from datetime import datetime, timezone, timedelta

# ── 경로 (engine.py 가 repo root에 위치) ──────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SIGNAL_HISTORY = os.path.join(BASE_DIR, "signal_history.csv")
HISTORY_CSV    = os.path.join(BASE_DIR, "history.csv")
RESULT_FILE    = os.path.join(BASE_DIR, "result.json")
FUND_FILE      = os.path.join(BASE_DIR, "fundamental.json")
FLOW_FILE      = os.path.join(BASE_DIR, "market_flow.json")
NEWS_FILE      = os.path.join(BASE_DIR, "news_scores.json")
TOKEN_FILE     = os.path.join(BASE_DIR, "kis_token.json")

# ── 상수 ──────────────────────────────────────────────
KST          = timezone(timedelta(hours=9))
KR_HOLIDAYS  = holidays.KR(years=[2025, 2026, 2027])
MAX_GAP_DAYS = 7
KIS_BASE     = "https://openapi.koreainvestment.com:9443"
TIMEOUT      = 10
MAX_RETRY    = 3
DELAY        = 0.2

TOP_N  = 20
TOP_CORE = 5
ENTRY_N  = 5

W_FLOW, W_MOM, W_VOL, W_FUND, W_NEWS = 0.30, 0.25, 0.15, 0.15, 0.15

BLOCK_KW = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
    "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
    "레버리지","인버스","선물","REIT","리츠","INDEX","지수"
]


# ═══════════════════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════════════════
def safe_float(v, d=0.0):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return d

def safe_int(v, d=0):
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return d

def tanh_norm(v):
    return (math.tanh(v) + 1) / 2

def zscore_norm(v, m, s):
    return tanh_norm((v - m) / s) if s > 0 else 0.5

def is_common_stock(code, name=""):
    code = str(code).strip()
    name = str(name or "").strip().upper()
    if not code.isdigit() or len(code) != 6:
        return False
    if code[-1] in ("5", "7", "9"):
        return False
    if name in ("", "NAN", "NONE"):
        return False
    return not any(k in name for k in BLOCK_KW)

def get_next_trading_day(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date() + timedelta(days=1)
    for _ in range(MAX_GAP_DAYS):
        if d.weekday() < 5 and d not in KR_HOLIDAYS:
            return d.strftime("%Y-%m-%d")
        d += timedelta(days=1)
    return None

def load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════
# KIS TOKEN / PRICE
# ═══════════════════════════════════════════════════════
def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        issued_str = data.get("issued_at", "").replace("Z", "") or "2000-01-01T00:00:00"
        issued = datetime.fromisoformat(issued_str)
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=KST)
        if (datetime.now(KST) - issued).seconds < 21600:
            return data.get("access_token")
    except Exception:
        pass

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
        except Exception:
            time.sleep(1)
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
    if not token:
        return {}
    for _ in range(MAX_RETRY):
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=kis_headers(token, "FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=TIMEOUT
            )
            if r.status_code == 401:
                return {}
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0":
                return {}
            o = d.get("output") or d.get("output1") or {}
            if isinstance(o, list):
                o = o[0] if o else {}
            return {
                "close"      : safe_int(o.get("stck_prpr")),
                "volume"     : safe_int(o.get("acml_vol")),
                "change_rate": safe_float(o.get("prdy_ctrt"))
            }
        except Exception:
            time.sleep(DELAY)
    return {}

def enrich_with_kis(stocks, token):
    if not token:
        return stocks
    enriched = []
    for s in stocks:
        code = str(s.get("code", "")).zfill(6)
        p = fetch_price_kis(token, code)
        if p and p.get("close", 0) > 0:
            s = {**s, **p}
        enriched.append(s)
        time.sleep(DELAY)
    print(f"[KIS] 가격 주입 완료: {len(enriched)}종목")
    return enriched


# ═══════════════════════════════════════════════════════
# DATA LOAD
# ═══════════════════════════════════════════════════════
def load_stock_data(today):
    try:
        df = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["code"] = df["code"].str.zfill(6)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return df[df["date"] == today].to_dict("records")
    except Exception as e:
        print(f"[LOAD ERROR] {e}")
        return []

def load_fundamental():
    raw = load_json(FUND_FILE)
    items = raw if isinstance(raw, list) else raw.get("stocks", [])
    return {str(s.get("code", "")).zfill(6): s for s in items}


# ═══════════════════════════════════════════════════════
# REGIME  ← history.csv 기반 + market_flow.json 병행
# ═══════════════════════════════════════════════════════
def compute_regime(today):
    # ① market_flow.json (우선)
    flow = load_json(FLOW_FILE)
    segs = ["KOSPI_foreign", "KOSPI_institution",
            "KOSDAQ_foreign", "KOSDAQ_institution"]
    flow_score = sum(flow.get(s, {}).get("score", 0) for s in segs) / 4
    flow_score = max(-1.0, min(1.0, flow_score))

    if abs(flow_score) > 0.3:
        regime = "UPTREND" if flow_score > 0 else "DOWNTREND"
        return regime, round(abs(flow_score), 2)

    # ② history.csv 20일 MA fallback
    try:
        df = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["date"]  = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        pivot = df.groupby("date")["close"].mean().sort_index()

        if len(pivot) >= 20:
            ma20    = pivot.rolling(20).mean().iloc[-1]
            last_cl = pivot.iloc[-1]
            std20   = pivot.rolling(20).std().iloc[-1] / last_cl if last_cl > 0 else 0.03

            if last_cl > ma20 * 1.02 and std20 < 0.04:
                conf = round(min(0.85, 0.5 + (last_cl / ma20 - 1) * 5), 2)
                return "UPTREND", conf
            elif last_cl < ma20 * 0.98:
                conf = round(min(0.80, 0.5 + (1 - last_cl / ma20) * 5), 2)
                return "DOWNTREND", conf
    except Exception as e:
        print(f"[REGIME FALLBACK ERR] {e}")

    return "SIDEWAY", 0.50


# ═══════════════════════════════════════════════════════
# FLOW MAP
# ═══════════════════════════════════════════════════════
def build_flow_map(flow):
    fm = {}
    for seg, w in [
        ("KOSPI_foreign",     0.36),
        ("KOSPI_institution", 0.24),
        ("KOSDAQ_foreign",    0.24),
        ("KOSDAQ_institution",0.16)
    ]:
        for r in flow.get(seg, {}).get("rows", []):
            c = str(r.get("code", "")).zfill(6)
            fm[c] = fm.get(c, 0) + safe_float(r.get("net")) * w
    return fm


# ═══════════════════════════════════════════════════════
# PRE-FILTER
# ═══════════════════════════════════════════════════════
def pre_filter(stocks, regime):
    cfg = {
        "UPTREND"  : (30000, 1000),
        "SIDEWAY"  : (20000, 1000),
        "DOWNTREND": (40000, 2000),
    }.get(regime, (20000, 1000))
    min_vol, min_price = cfg

    filtered = [
        s for s in stocks
        if safe_float(s.get("volume"))  >= min_vol
        and safe_float(s.get("close"))  >= min_price
        and is_common_stock(s.get("code", ""), s.get("name", ""))
    ]

    if len(filtered) < 15:
        filtered = [
            s for s in stocks
            if safe_float(s.get("volume"))  >= min_vol * 0.5
            and safe_float(s.get("close"))  >= min_price * 0.5
            and is_common_stock(s.get("code", ""), s.get("name", ""))
        ]

    return filtered


# ═══════════════════════════════════════════════════════
# SCORER
# ═══════════════════════════════════════════════════════
def score_stock(s, flow_map, flow_max, chg_mean, chg_std,
                vol_mean, vol_std, fund, news, regime):
    code = str(s.get("code", "")).zfill(6)
    chg  = safe_float(s.get("change_rate"))
    vol  = safe_float(s.get("volume"))
    fd   = fund.get(code, {})
    nv   = safe_float(news.get(code, 0))

    penalty = 1.0
    if chg > 10:
        penalty = max(0.65, 1 - (chg - 10) * 0.03)

    mom       = zscore_norm(chg, chg_mean, chg_std) * W_MOM * penalty
    vol_score = zscore_norm(vol, vol_mean, vol_std)  * W_VOL
    fund_sc   = (tanh_norm(safe_float(fd.get("roe")) / 10) if fd else 0.5) * W_FUND
    flow_sc   = tanh_norm(flow_map.get(code, 0) / (flow_max or 1) * 2.5) * W_FLOW
    news_sc   = tanh_norm(nv) * W_NEWS

    raw = flow_sc + mom + vol_score + fund_sc + news_sc

    if regime == "UPTREND":
        raw += 0.05
    elif regime == "DOWNTREND":
        raw -= 0.05

    return round(max(0, min(100, raw * 100)), 2)

def select_top20(stocks, flow_map, regime, fund, news):
    flow_vals = sorted([abs(v) for v in flow_map.values()])
    flow_max  = flow_vals[int(len(flow_vals) * 0.95) - 1] if flow_vals else 1.0

    vols = [safe_float(s.get("volume"))      for s in stocks]
    chgs = [safe_float(s.get("change_rate")) for s in stocks]
    vol_mean = sum(vols) / len(vols) if vols else 1
    vol_std  = (sum((v - vol_mean)**2 for v in vols) / len(vols))**0.5 if len(vols) > 1 else 1
    chg_mean = sum(chgs) / len(chgs) if chgs else 0
    chg_std  = (sum((c - chg_mean)**2 for c in chgs) / len(chgs))**0.5 if len(chgs) > 1 else 1

    scored = []
    for s in stocks:
        sc = score_stock(s, flow_map, flow_max, chg_mean, chg_std,
                         vol_mean, vol_std, fund, news, regime)
        scored.append((sc, s))
    scored.sort(reverse=True, key=lambda x: x[0])

    result = []
    for i, (sc, s) in enumerate(scored[:TOP_N], 1):
        code = str(s.get("code", "")).zfill(6)
        fd   = fund.get(code, {})
        chg  = safe_float(s.get("change_rate"))
        fq   = 1.0 if flow_map.get(code, 0) * chg > 0 else 0.7
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
            "debt_ratio"        : fd.get("debt_ratio")
        })
    return result


# ═══════════════════════════════════════════════════════
# ENTRY FILTER  ← prev_close 의존 제거 (FIX)
# ═══════════════════════════════════════════════════════
def entry_filter(stocks, flow_map, vol_mean, top20):
    candidates = []
    for s in stocks:
        code = str(s.get("code", "")).zfill(6)
        chg  = safe_float(s.get("change_rate"))
        vol  = safe_float(s.get("volume"))
        flow = flow_map.get(code, 0)

        if flow <= 0:
            continue
        if not (0.5 <= chg <= 6.0):
            continue
        if vol < vol_mean * 1.3:
            continue

        base_score = next((t["score"] for t in top20 if t["code"] == code), 50.0)
        vol_ratio  = vol / (vol_mean or 1)
        entry_sc   = round(base_score * 0.6 + min(vol_ratio, 3) * 10 + min(abs(flow), 3) * 10, 2)

        candidates.append({
            "rank"       : 0,
            "code"       : code,
            "name"       : s.get("name", ""),
            "entry_score": entry_sc,
            "base_score" : base_score,
            "price"      : int(safe_float(s.get("close"))),
            "change_pct" : chg
        })

    candidates.sort(key=lambda x: x["entry_score"], reverse=True)
    for i, c in enumerate(candidates[:ENTRY_N], 1):
        c["rank"] = i
    return candidates[:ENTRY_N]


# ═══════════════════════════════════════════════════════
# VERIFY  ← 주말/공휴일 자동 스킵 (FIX)
# ═══════════════════════════════════════════════════════
def verify(today):
    try:
        hist = pd.read_csv(HISTORY_CSV, dtype={"code": str}, encoding="utf-8-sig")
        hist["code"]  = hist["code"].str.zfill(6)
        hist["date"]  = pd.to_datetime(hist["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        hist["close"] = pd.to_numeric(hist["close"], errors="coerce")

        sig = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        sig_dates  = sorted(sig["date"].dropna().unique())
        prev_dates = [d for d in sig_dates if d < today]

        if not prev_dates:
            print("[VERIFY] 이전 신호 없음 — 데이터 축적 중")
            return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

        y        = prev_dates[-1]
        next_day = get_next_trading_day(y)
        eval_day = next_day if (next_day and next_day <= today) else today

        hist_eval = hist[hist["date"] == eval_day]
        if hist_eval.empty:
            print(f"[VERIFY] {eval_day} 데이터 없음")
            return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

        price_map = {k: v for k, v in zip(hist_eval["code"], hist_eval["close"])
                     if pd.notna(v) and float(v) > 0}

        sig_y = sig[sig["date"] == y]
        print(f"[VERIFY] 신호일:{y} → 평가일:{eval_day} | {len(sig_y)}종목 | 가격:{len(price_map)}개")

        hits, total, avg, top5 = 0, len(sig_y), 0, []
        for _, r in sig_y.iterrows():
            code  = str(r["code"]).zfill(6)
            entry = safe_float(r.get("price"))
            exitp = price_map.get(code, 0)
            if entry > 0 and exitp > 0:
                ret = (exitp - entry) / entry * 100
                avg += ret
                if ret > 0:
                    hits += 1
                if safe_float(r.get("rank", 999)) <= 5:
                    top5.append(ret)

        return {
            "win_rate"   : round(hits / total * 100, 1) if total else 0,
            "avg_return" : round(avg / total, 2)         if total else 0,
            "top5_return": round(sum(top5) / len(top5), 2) if top5 else 0
        }

    except Exception as e:
        print(f"[VERIFY ERROR] {e}")
        return {"win_rate": 0, "avg_return": 0, "top5_return": 0}


# ═══════════════════════════════════════════════════════
# SIGNAL HISTORY SAVE  ← leading zero 보존 (FIX)
# ═══════════════════════════════════════════════════════
def save_signal_history(top20, regime, today):
    df = pd.DataFrame([{
        "date"      : today,
        "regime"    : regime,
        "rank"      : t["rank"],
        "code"      : str(t["code"]).zfill(6),
        "name"      : t["name"],
        "score"     : t["score"],
        "price"     : t["price"],
        "change_pct": t["change_pct"]
    } for t in top20])

    try:
        old = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
        old["code"] = old["code"].str.zfill(6)
        if "date" in old.columns:
            old = old[old["date"] != today]
        df = pd.concat([old, df], ignore_index=True)
    except Exception:
        pass

    df.to_csv(SIGNAL_HISTORY, index=False, encoding="utf-8-sig")
    print(f"[SIGNAL] saved {len(df)} rows total")


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
def run():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"[START] engine v6.5.0  {today}")

    # 오늘 주식 데이터 로드
    stocks = load_stock_data(today)
    if not stocks:
        print("[NO DATA] history.csv 오늘 데이터 없음")
        return

    # 보조 데이터 로드
    flow = load_json(FLOW_FILE)
    news = load_json(NEWS_FILE).get("scores", {})
    fund = load_fundamental()

    # KIS 실시간 가격 보강 (선택적)
    token = get_token()
    if token:
        print("[KIS] 실시간 가격 주입 시작")
        stocks = enrich_with_kis(stocks, token)

    # 레짐 판단
    regime, confidence = compute_regime(today)
    print(f"[REGIME] {regime}  confidence={confidence}")

    # 필터링
    filtered = pre_filter(stocks, regime)
    print(f"[UNIVERSE] {len(filtered)}종목")

    if not filtered:
        print("[WARN] 필터링 후 종목 없음")
        return

    # 스코어링 + TOP20
    flow_map   = build_flow_map(flow)
    top20      = select_top20(filtered, flow_map, regime, fund, news)

    # TOP5 CORE
    top5_core = [t for t in top20[:TOP_CORE]
                 if t.get("expected_return_5d", 0) > 1.5]
    if not top5_core:
        top5_core = top20[:3]

    # ENTRY TOP5
    vols     = [safe_float(s.get("volume")) for s in filtered]
    vol_mean = sum(vols) / len(vols) if vols else 1
    entry_top5 = entry_filter(filtered, flow_map, vol_mean, top20)

    # 성과 검증
    perf = verify(today)

    # signal_history 저장
    save_signal_history(top20, regime, today)

    # result.json 저장 (index.html 완전 호환)
    result = {
        "date"             : today,
        "regime"           : regime,
        "universe_size"    : len(filtered),
        "confidence"       : confidence,
        "top20"            : top20,
        "top5_core"        : top5_core,
        "entry_top5"       : entry_top5,
        "performance_today": perf
    }

    with open(RESULT_FILE, "w", encoding="utf-8-sig") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] regime={regime} | top20={len(top20)} | entry={len(entry_top5)} | win_rate={perf['win_rate']}")


if __name__ == "__main__":
    run()
