import os, json, shutil, math, zipfile, requests, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from workalendar.asia import SouthKorea
from io import BytesIO

# =========================
# CONFIG
# =========================
KRX_API_KEY = os.getenv("KRX_API_KEY")

if not KRX_API_KEY:
    raise ValueError("KRX_API_KEY missing")

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

CACHE_FILE = "corp_map.json"
LAST_GOOD_FILE = "last_good_prices.json"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

cal = SouthKorea()

# =========================
# UTIL
# =========================
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
# PRICE (3-DAY FALLBACK)
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
    base = datetime.now(kst()).date()

    for i in range(3):
        d = (base - timedelta(days=i)).strftime("%Y%m%d")
        data = krx_call(KOSPI_URL, d) + krx_call(KOSDAQ_URL, d)

        if len(data) > 50:
            mp = {}

            for x in data:
                code = x.get("ISU_CD")
                close = float(x.get("TDD_CLSPRC", 0) or 0)
                val = float(x.get("ACC_TRDVAL", 0) or 0)

                if code and close > 0:
                    mp[code] = {
                        "close": close,
                        "val": val
                    }

            with open(LAST_GOOD_FILE, "w") as f:
                json.dump(mp, f)

            return mp

    try:
        return json.load(open(LAST_GOOD_FILE))
    except:
        return {}

# =========================
# SCORE ENGINE
# =========================
def score(price, rank):

    close = price.get("close", 0)
    val = price.get("val", 0)

    if close <= 0:
        return 0

    momentum = math.log1p(close)
    liquidity = math.log1p(val)

    turnover = val / close if close > 0 else 0
    risk = 1 / (1 + math.log1p(turnover))

    size = 20 - (rank / 200 * 20)

    return round(
        size * 0.2 +
        momentum * 0.4 +
        liquidity * 0.2 +
        risk * 0.2,
        2
    )

# =========================
# MAIN
# =========================
def main():

    prices = get_price_map()

    if len(prices) < 20:
        print("[WARN] degraded mode")

    universe = sorted(
        prices.keys(),
        key=lambda x: prices[x]["val"],
        reverse=True
    )[:200]

    results = []

    for i, code in enumerate(universe, 1):
        results.append({
            "code": code,
            "score": score(prices.get(code, {}), i)
        })

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "mode": "full" if len(prices) > 50 else "degraded",
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
