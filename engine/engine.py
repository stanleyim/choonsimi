import os, json, shutil, time, math, zipfile, requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from workalendar.asia import SouthKorea
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

KRX_API_KEY  = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

OUTPUT_PATH     = "data.json"
RAW_PATH        = "raw_data.json"
BACKUP_PATH     = "data.json.bak"
CORP_CACHE_FILE = "corp_map.json"

KRX_BASE   = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL  = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
DART_BASE  = "https://opendart.fss.or.kr/api"

cal = SouthKorea()
CORP_MAP, DART_CACHE = {}, {}

def safe_int(v):
    try: return int(str(v).replace(",", "").strip())
    except: return 0

def get_kst(): return timezone(timedelta(hours=9))

def get_trading_day():
    today = datetime.now(get_kst()).date()
    for i in range(10):
        d = today - timedelta(days=i)
        if cal.is_working_day(d):
            return d.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")

# ---------------- KRX ----------------
def call_krx(url, date):
    try:
        r = requests.get(
            url,
            params={"basDd": date},
            headers={"AUTH_KEY": KRX_API_KEY.strip()},
            timeout=5
        )
        j = r.json()
        return j.get("OutBlock_1") or j.get("block1") or []
    except:
        return []

def get_krx_fallback(url, base_date):
    base = datetime.strptime(base_date, "%Y%m%d").date()
    for i in range(7):
        d = base - timedelta(days=i)
        if not cal.is_working_day(d): continue
        data = call_krx(url, d.strftime("%Y%m%d"))
        if data: return data
        time.sleep(0.3)
    return []

def get_top200():
    date = get_trading_day()
    items = get_krx_fallback(KOSPI_URL, date) + get_krx_fallback(KOSDAQ_URL, date)
    cleaned = []
    for s in items:
        code = s.get("ISU_CD")
        name = s.get("ISU_NM")
        mcap = safe_int(s.get("MKTCAP", 0))
        if code and name and mcap > 0:
            cleaned.append((code, name, mcap))
    cleaned.sort(key=lambda x: x[2], reverse=True)
    return cleaned[:200]

# ---------------- DART ----------------
def load_corp_map():
    global CORP_MAP

    if os.path.exists(CORP_CACHE_FILE):
        try:
            with open(CORP_CACHE_FILE) as f:
                data = json.load(f)
                if len(data) > 1000:
                    CORP_MAP = data
                    print(f"[DART] cached {len(data)}")
                    return
        except:
            pass

    print("[DART] building corp_map...")
    r = requests.get(
        f"{DART_BASE}/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=30
    )

    z = zipfile.ZipFile(BytesIO(r.content))
    xml = z.read(z.namelist()[0])
    root = ET.fromstring(xml)

    for item in root.findall("list"):
        sc = item.findtext("stock_code", "").strip()
        cc = item.findtext("corp_code", "").strip()
        if sc:
            CORP_MAP[sc] = cc

    if len(CORP_MAP) < 1000:
        raise Exception("DART FAILED")

    with open(CORP_CACHE_FILE, "w") as f:
        json.dump(CORP_MAP, f)

    print(f"[DART] built {len(CORP_MAP)}")

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
            timeout=5
        )
        j = r.json()
        if j.get("status") == "000":
            return j.get("list", [])
    except:
        pass
    return []

def extract(fin_list):
    net = eq = debt = 0
    for i in fin_list:
        nm = i.get("account_nm","")
        v  = safe_int(i.get("thstrm_amount",0))
        if "당기순이익" in nm: net = v
        elif "자본총계" in nm: eq = v
        elif "부채총계" in nm: debt = v

    if eq > 0:
        return {
            "roe": round(min((net/eq)*100, 50), 2),
            "debt_ratio": round((debt/eq)*100, 2),
            "net": net
        }
    return None

def get_fin(code):
    if code in DART_CACHE:
        return DART_CACHE[code]

    corp = CORP_MAP.get(code)
    if not corp:
        return None

    year = datetime.now().year
    for y in [year-1, year-2]:
        data = get_financial(corp, y)
        if data:
            fin = extract(data)
            DART_CACHE[code] = fin
            return fin
    return None

# ---------------- 병렬 처리 ----------------
def fetch_fin(args):
    rank, code, name, mcap = args
    fin = get_fin(code)
    sc  = calc_score(rank, fin)
    sig = signal(sc, fin)

    return {
        "code": code,
        "name": name,
        "market_cap": mcap,
        "score": sc,
        "signal": sig,
        "roe": fin["roe"] if fin else None,
        "debt_ratio": fin["debt_ratio"] if fin else None
    }

# ---------------- SCORE ----------------
def calc_score(rank, fin):
    if not fin: return 0
    s = 20 - (rank/200*20)
    roe = fin.get("roe") or 0
    dr  = fin.get("debt_ratio") or 999
    s += math.log1p(max(roe,0))*10
    s += max(0, 40 - dr*0.2)
    if fin.get("net",0) > 0:
        s += 20
    return round(s,2)

def signal(sc, fin):
    if not fin: return "KRX_ONLY"
    if sc >= 70: return "STRONG_BUY"
    if sc >= 55: return "BUY"
    if sc >= 40: return "HOLD"
    if sc >= 25: return "WATCH"
    return "PASS"

# ---------------- MAIN ----------------
def main():
    print("[START] FINAL ENGINE")

    load_corp_map()

    tickers = get_top200()
    if not tickers:
        raise Exception("NO MARKET DATA")

    # 🔥 병렬 실행
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(fetch_fin, [
            (rank, code, name, mcap)
            for rank, (code, name, mcap) in enumerate(tickers, 1)
        ]))

    results.sort(key=lambda x:x["score"], reverse=True)

    kst = get_kst()
    output = {
        "version": datetime.now(kst).strftime("%Y%m%d_%H%M"),
        "generated_at": datetime.now(kst).isoformat(),
        "meta": {
            "count": len(results),
            "corp_map": len(CORP_MAP)
        },
        "top10": results[:10],
        "all": results
    }

    with open(RAW_PATH,"w") as f:
        json.dump(output,f,indent=2)

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    shutil.move(RAW_PATH, OUTPUT_PATH)

    print(f"[DONE] {len(results)} stocks")

if __name__ == "__main__":
    main()
