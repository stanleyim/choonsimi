import os, json, shutil, math, zipfile, requests, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from workalendar.asia import SouthKorea
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# =========================
# CONFIG
# =========================
KRX_API_KEY = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

if not KRX_API_KEY or not DART_API_KEY:
    raise ValueError("Missing API KEY")

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"
CORP_CACHE_FILE = "corp_map.json"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

DART_BASE = "https://opendart.fss.or.kr/api"
cal = SouthKorea()

# =========================
# GLOBAL STATE
# =========================
CORP_MAP = {}
CORP_LOADED = False

lock = Lock()
last_call = 0

# =========================
# RATE LIMIT (DART SAFE)
# =========================
def rate_limit():
    global last_call
    with lock:
        now = time.time()
        wait = 0.25 - (now - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.time()

# =========================
# UTIL
# =========================
def safe(v):
    try:
        return float(str(v).replace(",", "").strip())
    except:
        return 0.0

def kst():
    return timezone(timedelta(hours=9))

def trading_day():
    today = datetime.now(kst()).date()
    for i in range(10):
        d = today - timedelta(days=i)
        if cal.is_working_day(d):
            return d.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

# =========================
# KRX PRICE
# =========================
def krx_call(url, date):
    try:
        r = requests.get(
            url,
            params={"basDd": date},
            headers={"AUTH_KEY": KRX_API_KEY},
            timeout=5
        )
        if r.status_code != 200:
            return []
        j = r.json()
        return j.get("OutBlock_1") or []
    except:
        return []

def get_price_map():
    d = trading_day()
    data = krx_call(KOSPI_URL, d) + krx_call(KOSDAQ_URL, d)

    mp = {}
    for x in data:
        code = x.get("ISU_CD")
        if not code:
            continue

        close = safe(x.get("TDD_CLSPRC", 0))
        val = safe(x.get("ACC_TRDVAL", 0))

        if close <= 0:
            continue

        mp[code] = {"close": close, "val": val}

    return mp

# =========================
# CORP MAP
# =========================
def load_corp():
    global CORP_MAP, CORP_LOADED

    if CORP_LOADED:
        return

    if os.path.exists(CORP_CACHE_FILE):
        with open(CORP_CACHE_FILE) as f:
            CORP_MAP = json.load(f)
            CORP_LOADED = True
            return

    r = requests.get(
        f"{DART_BASE}/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=30
    )

    z = zipfile.ZipFile(BytesIO(r.content))
    xml = z.read(z.namelist()[0])
    root = ET.fromstring(xml)

    for i in root.findall("list"):
        sc = i.findtext("stock_code", "").strip()
        cc = i.findtext("corp_code", "").strip()
        if sc:
            CORP_MAP[sc] = cc

    with open(CORP_CACHE_FILE, "w") as f:
        json.dump(CORP_MAP, f)

    CORP_LOADED = True

# =========================
# DART FIN
# =========================
def fin_api(corp, year):
    try:
        rate_limit()
        r = requests.get(
            f"{DART_BASE}/fnlttSinglAcnt.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp,
                "bsns_year": str(year),
                "reprt_code": "11011"
            },
            timeout=5
        )
        j = r.json()
        if j.get("status") == "000":
            return j.get("list", [])
    except:
        return []

def extract(fin):
    net = eq = debt = 0

    for i in fin:
        nm = i.get("account_nm", "")
        v = safe(i.get("thstrm_amount", 0))

        if "당기순이익" in nm:
            net = v
        elif "자본총계" in nm:
            eq = v
        elif "부채총계" in nm:
            debt = v

    if eq <= 0:
        return None

    return {
        "roe": (net / eq) * 100,
        "debt": (debt / eq) * 100,
        "net": net
    }

def get_fin(code):
    corp = CORP_MAP.get(code)
    if not corp:
        return None

    y = datetime.now().year
    for year in [y-1, y-2]:
        data = fin_api(corp, year)
        if data:
            return extract(data)

    return None

# =========================
# SCORE ENGINE
# =========================
def score(fin, price, rank):

    close = price.get("close", 0)
    val = price.get("val", 0)

    momentum = math.log1p(close)
    liquidity = math.log1p(val)

    turnover = val / close if close > 0 else 0
    risk = 1 / (1 + math.log1p(turnover))

    fund = 0
    if fin:
        fund += math.log1p(max(fin["roe"], 0)) * 10
        fund += max(0, 50 - fin["debt"])
        if fin["net"] > 0:
            fund += 10

    size = 20 - (rank / 200 * 20)

    return round(
        size * 0.15 +
        momentum * 0.35 +
        liquidity * 0.20 +
        risk * 0.10 +
        fund * 0.20,
        2
    )

def signal(s):
    if s >= 70: return "STRONG_BUY"
    if s >= 55: return "BUY"
    if s >= 40: return "HOLD"
    if s >= 25: return "WATCH"
    return "PASS"

# =========================
# WORKER
# =========================
def worker(args, prices):
    rank, code = args
    fin = get_fin(code)
    sc = score(fin, prices.get(code, {}), rank)

    return {
        "code": code,
        "score": sc,
        "signal": signal(sc)
    }

# =========================
# MAIN
# =========================
def main():
    load_corp()
    prices = get_price_map()

    if len(prices) < 50:
        print("[SKIP] insufficient market data")
        return

    universe = sorted(
        [c for c in CORP_MAP.keys() if c in prices],
        key=lambda x: prices[x]["val"],
        reverse=True
    )[:200]

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(
            lambda x: worker(x, prices),
            [(i, c) for i, c in enumerate(universe, 1)]
        ))

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print("[DONE]", len(results))

if __name__ == "__main__":
    main()
