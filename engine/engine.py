"""
RegimeEngine v6.4.3-KIS-STABLE — OPERATION STABLE
─────────────────────────────────────────────────────
✔ KIS API 실시간 가격 + history.csv Fallback
✔ FIX: price 주입 → entry_filter 순서 변경 (price_history 생성 버그 해결)
✔ ADD: price > 0 필터링 (운영 안정화)
✔ ADD: prev_close 저장 (judge anchor용)
─────────────────────────────────────────────────────
"""

import json, math, pandas as pd, requests, os, time
from datetime import datetime, timedelta, timezone
import holidays

KST = timezone(timedelta(hours=9))
KR_HOLIDAYS = holidays.KR(years=[2025, 2026, 2027])
MAX_GAP_DAYS = 7
STABILITY_BOOST = 1.04

SIGNAL_HISTORY = "signal_history.csv"
HISTORY_CSV = "history.csv"
RESULT_FILE = "result.json"
PRICE_HISTORY = "price_history.csv"

TOP_N = 20
TOP_CORE = 5

W_FLOW, W_MOM, W_VOL, W_FUND, W_NEWS = 0.30, 0.25, 0.15, 0.15, 0.15

# KIS API Config
KIS_APP_KEY = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN = None

# ─────────────────────────────────────────────
# UTIL
# ─────────────────────────────────────────────
def safe_float(v, d=0.0):
    try:
        return float(v)
    except:
        return d

def tanh_norm(v):
    return (math.tanh(v) + 1) / 2

def zscore_norm(v, m, s):
    return tanh_norm((v - m) / s) if s > 0 else 0.5

def is_common_stock(name, code=""):
    if not name:
        return False
    code = str(code).strip()
    if code.isdigit() and len(code) == 6:
        if code[-1] in ("5", "7", "9"):
            return False
    name = str(name).upper()
    blacklist = [
        "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
        "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
        "LEVERAGE","INVERSE","레버리지","인버스","곱버스",
        "INDEX","지수"
    ]
    return not any(k in name for k in blacklist)

# ─────────────────────────────────────────────
# KIS API
# ─────────────────────────────────────────────
def get_kis_token():
    global KIS_TOKEN
    if KIS_TOKEN:
        return KIS_TOKEN
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        print("[KIS] No credentials, use history fallback")
        return None
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    headers = {"content-type":"application/json"}
    data = {
        "grant_type":"client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET
    }
    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        res.raise_for_status()
        KIS_TOKEN = res.json().get("access_token")
        print("[KIS] Token issued")
        return KIS_TOKEN
    except Exception as e:
        print(f"[KIS TOKEN ERROR] {e}")
        return None

def fetch_kis_price(code):
    token = get_kis_token()
    if not token:
        return None, None
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type":"application/json",
        "authorization":f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id":"FHKST01010100"
    }
    params = {"FID_COND_MRKT_DIV_CODE":"J", "FID_INPUT_ISCD": code}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        res.raise_for_status()
        data = res.json().get("output", {})
        price = safe_float(data.get("stck_prpr"))
        prev_cl = safe_float(data.get("stck_prdy_clpr"))
        return price, prev_cl
    except Exception as e:
        print(f"[KIS PRICE ERROR] {code}: {e}")
        return None, None

# ─────────────────────────────────────────────
# SCORER
# ─────────────────────────────────────────────
class StockScorer:
    def __init__(self, stocks, flow, regime, fund, news, prev_top_codes=None):
        self.stocks = stocks
        self.flow = flow
        self.regime = regime
        self.fund = fund
        self.news = news
        self.prev_top_codes = set(prev_top_codes or [])

        chg_list = [safe_float(s.get("change_rate")) for s in stocks]
        vol_list = [safe_float(s.get("volume")) for s in stocks]

        self.chg_mean = sum(chg_list)/len(chg_list) if chg_list else 0
        self.chg_std = (sum((c-self.chg_mean)**2 for c in chg_list)/len(chg_list))**0.5 if len(chg_list)>1 else 1
        self.vol_mean = sum(vol_list)/len(vol_list) if vol_list else 1
        self.vol_std = (sum((v-self.vol_mean)**2 for v in vol_list)/len(vol_list))**0.5 if len(vol_list)>1 else 1

        self.flow_map = {}
        for seg, w in [
            ("KOSPI_foreign",0.36),
            ("KOSPI_institution",0.24),
            ("KOSDAQ_foreign",0.24),
            ("KOSDAQ_institution",0.16)
        ]:
            for r in self.flow.get(seg,{}).get("rows",[]):
                c = str(r.get("code","")).zfill(6)
                self.flow_map[c] = self.flow_map.get(c,0) + safe_float(r.get("net")) * w

        vals = sorted([abs(v) for v in self.flow_map.values()])
        self.flow_max = vals[int(len(vals)*0.95)-1] if vals else 1.0

    def score(self, s):
        code = str(s.get("code","")).zfill(6)
        chg = safe_float(s.get("change_rate"))
        vol = safe_float(s.get("volume"))
        name = s.get("name","")

        if not is_common_stock(name, code):
            return 0.0, 0.0

        flow_s = tanh_norm(self.flow_map.get(code,0) / self.flow_max)
        mom_s = zscore_norm(chg, self.chg_mean, self.chg_std)
        vol_s = zscore_norm(vol, self.vol_mean, self.vol_std)

        base = W_FLOW*flow_s + W_MOM*mom_s + W_VOL*vol_s + W_FUND*0.5 + W_NEWS*0.5
        if code in self.prev_top_codes:
            base *= STABILITY_BOOST

        return round(base * 100, 2), round(chg, 2)

# ─────────────────────────────────────────────
# FILTER & SELECT
# ─────────────────────────────────────────────
def entry_filter(s):
    chg = safe_float(s.get("change_rate"))
    vol = safe_float(s.get("volume"))
    price = safe_float(s.get("price"))

    # 운영 안정화: price 0 제외
    if chg <= 0 or vol < 0 or price <= 0:
        return False
    return 0.5 <= chg <= 6.0

def select_top20(stocks, scorer, hdf, today):
    scored = []
    for s in stocks:
        score, chg = scorer.score(s)
        if score <= 0:
            continue

        code = s["code"]
        # KIS 가격 먼저 주입 - 순서 중요
        price, prev_cl = fetch_kis_price(code)
        if price is None or price <= 0:
            hrow = hdf[(hdf["code"]==code) & (hdf["date"]==today)]
            price = safe_float(hrow["close"]) if not hrow.empty else 0
            phrow = hdf[(hdf["code"]==code) & (hdf["date"]<today)].sort_values("date").tail(1)
            prev_cl = safe_float(phrow["close"]) if not phrow.empty else 0

        scored.append({
            "code": code,
            "name": s.get("name",""),
            "score": score,
            "change_pct": chg,
            "price": price,
            "prev_close": prev_cl
        })
        time.sleep(0.2) # KIS rate limit

    scored.sort(key=lambda x: x["score"], reverse=True)
    top20 = scored[:TOP_N]

    # price가 있는 상태에서 필터링
    entry_top5 = [x for x in top20 if entry_filter(x)][:TOP_CORE]
    if not entry_top5: # Fallback: 조건 통과 0개면 상위 5개
        entry_top5 = top20[:TOP_CORE]
    return {"top20": top20, "entry_top5": entry_top5}

# ─────────────────────────────────────────────
# PRICE HISTORY
# ─────────────────────────────────────────────
def save_price_history(result, history_df, path=PRICE_HISTORY):
    try:
        today = result.get("date")
        # entry_top5 우선, 없으면 top20으로 Fallback
        target_list = result.get("entry_top5", []) or result.get("top20", [])
        if not target_list:
            print("[PRICE] no data")
            return

        rows = []
        for x in target_list:
            code = x["code"]
            price = safe_float(x.get("price"))
            prev_cl = safe_float(x.get("prev_close"))
            rows.append({
                "date": today,
                "code": code,
                "entry_price": price,
                "prev_close": prev_cl,
                "change_pct": safe_float(x.get("change_pct"))
            })

        df = pd.DataFrame(rows)
        if os.path.exists(path):
            old = pd.read_csv(path, dtype={"code":str})
            df = pd.concat([old, df]).drop_duplicates(subset=["date","code"], keep="last")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[PRICE] saved {len(rows)} rows")
    except Exception as e:
        print(f"[PRICE ERROR] {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run():
    try:
        df = pd.read_csv(SIGNAL_HISTORY, dtype={"code":str}, encoding="utf-8-sig")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"])
    except:
        print("[NO HISTORY]")
        return

    try:
        hdf = pd.read_csv(HISTORY_CSV, dtype={"code":str}, encoding="utf-8-sig")
        hdf["date"] = pd.to_datetime(hdf["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    except:
        hdf = pd.DataFrame()

    df["code"] = df["code"].str.zfill(6)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    today_df = df[df["date"] == today].to_dict("records")

    if not today_df:
        print("[NO DATA]")
        return

    prev_top_codes = []
    try:
        with open(RESULT_FILE,"r",encoding="utf-8-sig") as f:
            prev = json.load(f)
            prev_top_codes = [x["code"] for x in prev.get("entry_top5",[])]
    except:
        pass

    flow = {
        "KOSPI_foreign":{"rows":[]},
        "KOSPI_institution":{"rows":[]},
        "KOSDAQ_foreign":{"rows":[]},
        "KOSDAQ_institution":{"rows":[]}
    }
    fund = {}
    news = {}

    scorer = StockScorer(today_df, flow, "UPTREND", fund, news, prev_top_codes)
    result_data = select_top20(today_df, scorer, hdf, today) # hdf, today 전달

    result = {
        "date": today,
        "regime": "UPTREND",
        "confidence": 0.5,
        "top20": result_data["top20"],
        "entry_top5": result_data["entry_top5"]
    }

    with open(RESULT_FILE,"w",encoding="utf-8-sig") as f:
        json.dump(result,f,indent=2,ensure_ascii=False)

    save_price_history(result, hdf)
    print("[DONE] v6.4.3-KIS-STABLE", today)

if __name__ == "__main__":
    run()
