"""
engine.py — v6.4.5-KIS-FIX
─────────────────────────────────────────────
✔ KIS 실시간 가격 + 거래량 주입
✔ 시장 국면 Regime + 신뢰도 Confidence 계산
✔ change_rate 자동 계산으로 KeyError 해결
─────────────────────────────────────────────
"""

import os, json, pandas as pd, numpy as np, time
from datetime import datetime, timezone, timedelta
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # root/

SIGNAL_HISTORY = os.path.join(BASE_DIR, "signal_history.csv")
HISTORY_CSV = os.path.join(BASE_DIR, "history.csv")
RESULT_FILE = os.path.join(BASE_DIR, "result.json")
PRICE_HISTORY = os.path.join(BASE_DIR, "price_history.csv")

KST = timezone(timedelta(hours=9))
KIS_BASE = "https://openapi.koreainvestment.com:9443"
TOKEN_FILE = os.path.join(BASE_DIR, "kis_token.json")
TIMEOUT = 10
MAX_RETRY = 3
DELAY = 0.2

TARGET_COUNT = 20
ENTRY_COUNT = 5

GAP_THRESH = 0.015
STD_THRESH = 0.02
PCT_MIN = 3.0
PCT_MAX = 8.0
VOL_MIN = 300_000
VOL_MAX = 150_000_000

BLOCK_KEYWORDS = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
    "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
    "레버리지","인버스","선물","REIT","리츠"
]

def safe_float(v):
    try:
        return float(str(v).replace(",",""))
    except:
        return 0.0

def safe_int(v):
    try:
        return int(str(v).replace(",",""))
    except:
        return 0

def is_common_stock(code, name):
    code = str(code).strip()
    name = str(name or "").strip()
    if not code.isdigit() or len(code)!= 6:
        return False
    if code[-1] in ("5","7","9"):
        return False
    if name.lower() in ("", "nan", "none"):
        return False
    if any(k in name.upper() for k in BLOCK_KEYWORDS):
        return False
    return True

def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        issued = datetime.fromisoformat(data.get("issued_at","").replace("Z","") or "2000-01-01T00:00:00")
        if (datetime.now(KST) - issued).seconds < 21600:
            return data.get("access_token")
    except:
        pass

    for _ in range(MAX_RETRY):
        try:
            r = requests.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={
                    "grant_type":"client_credentials",
                    "appkey":os.environ.get("KIS_APP_KEY",""),
                    "appsecret":os.environ.get("KIS_APP_SECRET","")
                },
                timeout=TIMEOUT
            )
            r.raise_for_status()
            token = r.json().get("access_token")
            with open(TOKEN_FILE,"w",encoding="utf-8-sig") as f:
                json.dump({"access_token":token,"issued_at":datetime.now(KST).isoformat()}, f)
            return token
        except:
            time.sleep(1)
    return None

def headers(token, tr_id):
    return {
        "authorization": f"Bearer {token}",
        "appkey": os.environ.get("KIS_APP_KEY",""),
        "appsecret": os.environ.get("KIS_APP_SECRET",""),
        "tr_id": tr_id,
        "content-type": "application/json",
        "custtype": "P"
    }

def fetch_price_kis(token, code):
    if not token:
        return {}
    for _ in range(MAX_RETRY):
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=headers(token, "FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":code},
                timeout=TIMEOUT
            )
            if r.status_code == 401:
                return {}
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd")!= "0":
                return {}
            o = d.get("output") or d.get("output1") or {}
            if isinstance(o, list):
                o = o[0] if o else {}
            return {
                "code": code,
                "name": o.get("hts_kor_isnm",""),
                "close": safe_int(o.get("stck_prpr")),
                "prev_close": safe_int(o.get("stck_prdy_clpr")),
                "volume": safe_int(o.get("acml_vol")),
                "change_rate": safe_float(o.get("prdy_ctrt"))
            }
        except:
            time.sleep(DELAY)
    return {}

def load_signal_history():
    try:
        df = pd.read_csv(SIGNAL_HISTORY, dtype={"code":str}, encoding="utf-8-sig")
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df
    except:
        print("[NO HISTORY]")
        return pd.DataFrame()

def calc_regime(df):
    """시장 국면 + 신뢰도 계산. 20일 이동평균 기준"""
    if len(df) < 20:
        return "sideways", 0.5

    ma20 = df["close"].rolling(20).mean().iloc[-1]
    last_close = df["close"].iloc[-1]
    std20 = df["close"].rolling(20).std().iloc[-1] / last_close if last_close > 0 else 0.03

    if last_close > ma20 * 1.02 and std20 < 0.03:
        return "trending_up", 0.85
    elif last_close < ma20 * 0.98:
        return "trending_down", 0.65
    else:
        return "sideways", 0.5

def entry_filter(df):
    if df.empty:
        return pd.DataFrame()
    df["gap"] = (df["close"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan)
    df["vol"] = df["volume"]
    mask = (
        (df["gap"].between(GAP_THRESH, 0.10)) &
        (df["change_rate"].between(PCT_MIN, PCT_MAX)) &
        (df["vol"].between(VOL_MIN, VOL_MAX))
    )
    filtered = df[mask].copy()
    print(f"[FILTER] {len(df)} → {len(filtered)} after entry_filter")
    return filtered

def pick_top20(df):
    if df.empty:
        return pd.DataFrame()
    
    # change_rate 없으면 close/prev_close로 계산
    if "change_rate" not in df.columns:
        df["change_rate"] = (df["close"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan)
        df["change_rate"] = df["change_rate"] * 100 # % 단위 변환

    df["score"] = df["change_rate"] * np.log1p(df["volume"])
    return df.nlargest(TARGET_COUNT, "score").copy()

def pick_entry_top5(df, confidence):
    if df.empty:
        return pd.DataFrame()

    # 신뢰도 0.7 이상: 5개, 0.5 이상: 3개, 미만: 0개
    if confidence >= 0.7:
        entry_count = 5
    elif confidence >= 0.5:
        entry_count = 3
    else:
        entry_count = 0
        print("[REGIME] Low confidence. Skip entry.")

    df["entry_score"] = df["gap"] * np.log1p(df["volume"])
    return df.nlargest(entry_count, "entry_score").copy()

def save_price_history(result):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if result.empty:
        return
    out = result[["code","name","close","prev_close","volume"]].copy()
    out["date"] = today
    out["change_rate"] = (out["close"] - out["prev_close"]) / out["prev_close"].replace(0, np.nan)
    out = out[["date","code","name","close","prev_close","volume","change_rate"]]

    try:
        prev = pd.read_csv(PRICE_HISTORY, encoding="utf-8-sig")
        prev = prev[prev["date"]!= today]
        final = pd.concat([prev, out], ignore_index=True)
    except:
        final = out

    final.to_csv(PRICE_HISTORY, index=False, encoding="utf-8-sig")
    print(f"[PRICE] saved {len(out)} rows")

def run():
    print("[START] v6.4.5-KIS-FIX", datetime.now(KST).strftime("%Y-%m-%d"))
    token = get_token()

    hdf = load_signal_history()
    if hdf.empty:
        return

    # 시장 국면 계산
    market_regime, confidence = calc_regime(hdf)
    print(f"[REGIME] {market_regime} confidence={confidence:.2f}")

    top20 = pick_top20(hdf)

    if token and not top20.empty:
        prices = []
        for code in top20["code"].unique():
            d = fetch_price_kis(token, code)
            if d and is_common_stock(d["code"], d["name"]):
                prices.append(d)
            time.sleep(DELAY)
        if prices:
            top20 = pd.DataFrame(prices)

    entry = entry_filter(top20)
    entry_top5 = pick_entry_top5(entry, confidence)

    result = {
        "generated_at": datetime.now(KST).isoformat(),
        "market_regime": market_regime,
        "confidence": round(confidence, 2),
        "top20": top20.to_dict(orient="records"),
        "entry_top5": entry_top5.to_dict(orient="records")
    }

    with open(RESULT_FILE, "w", encoding="utf-8-sig") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    save_price_history(entry_top5)
    print("[DONE] v6.4.5-KIS-FIX", datetime.now(KST).strftime("%Y-%m-%d"))

if __name__ == "__main__":
    run()
