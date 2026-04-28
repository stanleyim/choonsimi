import os
import json
import shutil
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import requests
from workalendar.asia import SouthKorea

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
KRX_API_KEY  = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"
CORP_CACHE  = "corp_map.json"

KRX_BASE   = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL  = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

DART_BASE  = "https://opendart.fss.or.kr/api"

cal = SouthKorea()
CORP_MAP = None

# ─────────────────────────────
# UTIL
# ─────────────────────────────
def safe_int(v):
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except:
        return 0

def get_kst():
    return timezone(timedelta(hours=9))

def get_trading_day():
    today = datetime.now(get_kst()).date()
    for i in range(10):
        d = today - timedelta(days=i)
        if cal.is_working_day(d):
            return d.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

# ─────────────────────────────
# CORP MAP (FULL CACHE)
# ─────────────────────────────
def load_corp_map():
    global CORP_MAP

    if CORP_MAP:
        return CORP_MAP

    if os.path.exists(CORP_CACHE):
        with open(CORP_CACHE, "r", encoding="utf-8") as f:
            CORP_MAP = json.load(f)
            print(f"[DART] corp_map loaded: {len(CORP_MAP)}")
            return CORP_MAP

    print("[DART] downloading corpCode.xml ...")

    r = requests.get(
        f"{DART_BASE}/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=30
    )

    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_data = z.read("CORPCODE.xml")

    root = ET.fromstring(xml_data)

    CORP_MAP = {}

    for item in root.findall("list"):
        stock_code = item.findtext("stock_code")
        corp_code  = item.findtext("corp_code")

        if stock_code and corp_code:
            CORP_MAP[stock_code.strip()] = corp_code.strip()

    with open(CORP_CACHE, "w", encoding="utf-8") as f:
        json.dump(CORP_MAP, f, ensure_ascii=False)

    print(f"[DART] corp_map saved: {len(CORP_MAP)}")

    return CORP_MAP

def get_corp_code(stock_code):
    return load_corp_map().get(stock_code)

# ─────────────────────────────
# KRX DATA
# ─────────────────────────────
def get_krx(url, date):
    try:
        r = requests.post(
            url,
            headers={
                "AUTH_KEY": KRX_API_KEY.strip(),
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={"basDd": date},
            timeout=20
        )
        data = r.json()
        return data.get("OutBlock_1") or data.get("block1") or []
    except:
        return []

def get_top200():
    date = get_trading_day()

    kospi  = get_krx(KOSPI_URL, date)
    kosdaq = get_krx(KOSDAQ_URL, date)

    items = kospi + kosdaq

    cleaned = []
    for s in items:
        code = s.get("ISU_CD", "")
        name = s.get("ISU_NM", "")
        mcap = safe_int(s.get("MKTCAP", 0))

        if code and name:
            cleaned.append((code, name, mcap))

    cleaned.sort(key=lambda x: x[2], reverse=True)
    return cleaned[:200]

# ─────────────────────────────
# DART FINANCE
# ─────────────────────────────
def get_financial(corp_code, year):
    try:
        r = requests.get(
            f"{DART_BASE}/fnlttSinglAcnt.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011"
            },
            timeout=10
        )
        j = r.json()
        if j.get("status") == "000":
            return j.get("list", [])
    except:
        pass
    return []

def extract(fin_list):
    r = {"roe":0,"debt_ratio":999,"net":0,"equity":0,"debt":0}

    for i in fin_list:
        nm = i.get("account_nm","")
        v  = safe_int(i.get("thstrm_amount",0))

        if "당기순이익" in nm:
            r["net"] = v
        elif "자본총계" in nm:
            r["equity"] = v
        elif "부채총계" in nm:
            r["debt"] = v

    if r["equity"] > 0:
        r["roe"] = round(r["net"]/r["equity"]*100,2)
        r["debt_ratio"] = round(r["debt"]/r["equity"]*100,2)

    return r

def get_fin(stock_code):
    corp = get_corp_code(stock_code)

    if not corp:
        return None

    year = datetime.now().year

    for y in [year-1, year-2]:
        fin = get_financial(corp, y)
        if fin:
            return extract(fin)

    return None

# ─────────────────────────────
# SCORING ENGINE
# ─────────────────────────────
def score(rank, fin):
    if not fin:
        return 0

    s = 20 - (rank/200*20)
    s += min(fin["roe"],50)*0.5
    s += max(0, 40 - fin["debt_ratio"]*0.2)

    if fin["net"] > 0:
        s += 20

    return round(s,2)

def signal(sc, fin):
    if not fin:
        return "KRX_ONLY"
    if sc >= 70:
        return "STRONG_BUY"
    if sc >= 55:
        return "BUY"
    if sc >= 40:
        return "HOLD"
    if sc >= 25:
        return "WATCH"
    return "PASS"

# ─────────────────────────────
# MAIN ENGINE
# ─────────────────────────────
def main():
    print("[START] V4 PRODUCTION ENGINE")

    tickers = get_top200()
    results = []

    for rank,(code,name,mcap) in enumerate(tickers,1):
        print(f"[{rank}/200] {name}")

        fin = get_fin(code)
        sc  = score(rank,fin)
        sig = signal(sc,fin)

        results.append({
            "code":code,
            "name":name,
            "mcap":mcap,
            "score":sc,
            "signal":sig,
            "roe": fin["roe"] if fin else 0,
            "debt_ratio": fin["debt_ratio"] if fin else 0
        })

        time.sleep(0.05)  # ⚡ FAST MODE (production)

    results.sort(key=lambda x:x["score"], reverse=True)

    output = {
        "version": datetime.now().strftime("%Y%m%d_%H%M"),
        "generated_at": datetime.now().isoformat(),
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH,"w",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)

    print("\n[DONE] COMPLETE")

    for i,r in enumerate(results[:10],1):
        print(f"{i}. {r['name']} | {r['score']} | {r['signal']}")

if __name__ == "__main__":
    main()
