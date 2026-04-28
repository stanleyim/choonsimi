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

KRX_BASE   = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL  = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

DART_BASE  = "https://opendart.fss.or.kr/api"

cal = SouthKorea()

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
# KRX
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

def get_krx_fallback(url, base_date):
    base = datetime.strptime(base_date, "%Y%m%d").date()

    for i in range(7):
        day = base - timedelta(days=i)
        if not cal.is_working_day(day):
            continue

        data = get_krx(url, day.strftime("%Y%m%d"))
        if data:
            return data

    return []

def get_top200():
    date = get_trading_day()

    kospi  = get_krx_fallback(KOSPI_URL, date)
    kosdaq = get_krx_fallback(KOSDAQ_URL, date)

    all_items = kospi + kosdaq

    cleaned = []
    for s in all_items:
        code = s.get("ISU_CD", "")
        name = s.get("ISU_NM", "")
        mcap = safe_int(s.get("MKTCAP", 0))

        if code and name:
            cleaned.append((code, name, mcap))

    cleaned.sort(key=lambda x: x[2], reverse=True)
    return cleaned[:200]

# ─────────────────────────────
# DART CORP MAP (핵심 FIX)
# ─────────────────────────────
CORP_MAP = None

def load_corp_map():
    global CORP_MAP

    if CORP_MAP:
        return CORP_MAP

    print("[DART] loading corpCode.xml ...")

    url = f"{DART_BASE}/corpCode.xml"
    params = {"crtfc_key": DART_API_KEY}

    r = requests.get(url, params=params)

    zip_path = "corp.zip"
    with open(zip_path, "wb") as f:
        f.write(r.content)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall("corp")

    tree = ET.parse("corp/CORPCODE.xml")
    root = tree.getroot()

    CORP_MAP = {}

    for item in root.findall("list"):
        stock_code = item.findtext("stock_code")
        corp_code = item.findtext("corp_code")

        if stock_code and corp_code:
            CORP_MAP[stock_code] = corp_code

    print(f"[DART] loaded corp_map: {len(CORP_MAP)}")

    return CORP_MAP

def get_corp_code(stock_code):
    return load_corp_map().get(stock_code)

# ─────────────────────────────
# DART FINANCIAL
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

        data = r.json()
        if data.get("status") == "000":
            return data.get("list", [])

    except:
        pass

    return []

def extract(fin_list):
    r = {
        "roe": 0.0,
        "debt_ratio": 999.0,
        "net": 0,
        "equity": 0,
        "debt": 0
    }

    for i in fin_list:
        nm = i.get("account_nm", "")
        v  = safe_int(i.get("thstrm_amount", 0))

        if "당기순이익" in nm:
            r["net"] = v
        elif "자본총계" in nm:
            r["equity"] = v
        elif "부채총계" in nm:
            r["debt"] = v

    if r["equity"] > 0:
        r["roe"] = round((r["net"] / r["equity"]) * 100, 2)
        r["debt_ratio"] = round((r["debt"] / r["equity"]) * 100, 2)

    return r

def get_fin(stock_code):
    corp = get_corp_code(stock_code)

    if not corp:
        print(f"[DART] corp 없음: {stock_code}")
        return None

    year = datetime.now().year

    for y in [year - 1, year - 2]:
        fin = get_financial(corp, y)
        if fin:
            return extract(fin)

    return None

# ─────────────────────────────
# SCORE ENGINE
# ─────────────────────────────
def score(rank, fin):
    if not fin:
        return 0

    s = 20 - (rank / 200 * 20)

    s += min(fin["roe"], 50) * 0.5
    s += max(0, 40 - fin["debt_ratio"] * 0.2)

    if fin["net"] > 0:
        s += 20

    return round(s, 2)

def signal(score_val, fin):
    if not fin:
        return "KRX_ONLY"
    if score_val >= 70:
        return "STRONG_BUY"
    if score_val >= 55:
        return "BUY"
    if score_val >= 40:
        return "HOLD"
    if score_val >= 25:
        return "WATCH"
    return "PASS"

# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main():
    print("[START] ENGINE vFINAL")

    tickers = get_top200()
    if not tickers:
        print("[FAIL] KRX 없음")
        return

    results = []

    for rank, (code, name, mcap) in enumerate(tickers, 1):
        print(f"[{rank}/200] {name}")

        fin = get_fin(code)
        sc  = score(rank, fin)
        sig = signal(sc, fin)

        results.append({
            "code": code,
            "name": name,
            "market_cap": mcap,
            "score": sc,
            "signal": sig,
            "roe": fin["roe"] if fin else 0,
            "debt_ratio": fin["debt_ratio"] if fin else 0
        })

        time.sleep(0.15)

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "version": datetime.now().strftime("%Y%m%d_%H%M"),
        "generated_at": datetime.now().isoformat(),
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\n[DONE] 200개 처리 완료")

    for i, r in enumerate(results[:10], 1):
        print(f"{i}. {r['name']} | {r['score']} | {r['signal']}")

if __name__ == "__main__":
    main()
