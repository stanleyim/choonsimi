import os
import json
import shutil
import time
import requests
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
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
# SAFE KRX (핵심 안정화)
# ─────────────────────────────
def get_krx(url, date):
    try:
        r = requests.post(
            url,
            headers={
                "AUTH_KEY": (KRX_API_KEY or "").strip(),
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={"basDd": date},
            timeout=20
        )

        if r.status_code != 200:
            print("[KRX ERROR] HTTP:", r.status_code)
            return []

        try:
            data = r.json()
        except:
            print("[KRX ERROR] invalid json")
            return []

        result = (
            data.get("OutBlock_1")
            or data.get("block1")
            or data.get("OutBlock_1", [])
            or []
        )

        print(f"[KRX] {url.split('/')[-1]} -> {len(result)} rows")
        return result

    except Exception as e:
        print("[KRX EXCEPTION]", str(e))
        return []

# ─────────────────────────────
# TOP200 SAFE
# ─────────────────────────────
def get_top200():
    date = get_trading_day()

    print("[KRX] date =", date)

    kospi  = get_krx(KOSPI_URL, date)
    kosdaq = get_krx(KOSDAQ_URL, date)

    # 🔥 HARD FALLBACK (핵심)
    if not kospi and not kosdaq:
        print("[KRX WARNING] empty response → retry fallback date")
        date = get_trading_day()
        kospi  = get_krx(KOSPI_URL, date)
        kosdaq = get_krx(KOSDAQ_URL, date)

    if not kospi and not kosdaq:
        print("[FAIL] KRX completely empty")
        return []

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
# CORP MAP (SAFE CACHE)
# ─────────────────────────────
def load_corp_map():
    global CORP_MAP

    if CORP_MAP:
        return CORP_MAP

    if os.path.exists(CORP_CACHE):
        with open(CORP_CACHE, "r", encoding="utf-8") as f:
            CORP_MAP = json.load(f)
            print("[DART] corp_map loaded:", len(CORP_MAP))
            return CORP_MAP

    print("[DART] downloading corp map...")

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

    print("[DART] saved:", len(CORP_MAP))

    return CORP_MAP

def get_corp_code(stock_code):
    return load_corp_map().get(stock_code)

# ─────────────────────────────
# MAIN SAFETY CHECK
# ─────────────────────────────
def main():
    print("[START] V4.1 SAFE ENGINE")

    tickers = get_top200()

    if not tickers:
        print("[FATAL] NO MARKET DATA → STOP")
        return

    print("[OK] tickers:", len(tickers))

    # 여기부터 기존 로직 그대로 붙이면 됨
    results = []

    for i, (code, name, mcap) in enumerate(tickers, 1):
        print(f"[{i}/200] {name}")
        results.append({
            "code": code,
            "name": name,
            "mcap": mcap
        })
        time.sleep(0.03)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("[DONE] SAFE MODE COMPLETE")

if __name__ == "__main__":
    main()
