import os
import json
import shutil
import time
import math
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import requests
from workalendar.asia import SouthKorea

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

CORP_MAP   = {}
DART_CACHE = {}

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

def call_krx(url, date):
    try:
        r = requests.post(
            url,
            data={"basDd": date},
            headers={
                "AUTH_KEY":     KRX_API_KEY.strip(),
                "Accept":       "application/json",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=20
        )
        print(f"[KRX] HTTP {r.status_code} | {len(r.content)} bytes")
        j = r.json()
        ob1 = j.get("OutBlock_1") or []
        bl1 = j.get("block1")     or []
        print(f"[KRX] OutBlock_1={len(ob1)} block1={len(bl1)}")
        return ob1 or bl1
    except Exception as e:
        print(f"[KRX ERROR] {type(e).__name__}: {str(e)[:80]}")
        return []

def get_krx_fallback(url, base_date):
    base = datetime.strptime(base_date, "%Y%m%d").date()
    tag  = "KOSPI" if "stk_bydd" in url else "KOSDAQ"
    for i in range(7):
        day = base - timedelta(days=i)
        if not cal.is_working_day(day):
            continue
        print(f"[KRX-{tag}] {day} 시도...")
        data = call_krx(url, day.strftime("%Y%m%d"))
        if data:
            print(f"[KRX-{tag}] {day} 성공 ({len(data)}개)")
            return data
        time.sleep(0.5)
    print(f"[KRX-{tag}] 전체 실패")
    return []

def get_top200():
    date   = get_trading_day()
    print(f"[KRX] 기준일: {date}")
    kospi  = get_krx_fallback(KOSPI_URL,  date)
    kosdaq = get_krx_fallback(KOSDAQ_URL, date)
    all_items = kospi + kosdaq
    if not all_items:
        return []
    print(f"[KRX] KOSPI {len(kospi)}개 + KOSDAQ {len(kosdaq)}개")
    cleaned = []
    for s in all_items:
        code = s.get("ISU_CD", "")
        name = s.get("ISU_NM", "")
        mcap = safe_int(s.get("MKTCAP", 0))
        if code and name and mcap > 0:
            cleaned.append((code, name, mcap))
    cleaned.sort(key=lambda x: x[2], reverse=True)
    print(f"[KRX] TOP {min(len(cleaned), 200)}개 확정")
    return cleaned[:200]

def load_corp_map():
    global CORP_MAP
    if os.path.exists(CORP_CACHE_FILE):
        try:
            with open(CORP_CACHE_FILE, "r", encoding="utf-8") as f:
                CORP_MAP = json.load(f)
                print(f"[DART] corp_map loaded ({len(CORP_MAP)}개)")
                return
        except:
            pass
    print("[DART] corpCode.xml 다운로드 중...")
    try:
        r = requests.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
            timeout=30
        )
        with open("corp.zip", "wb") as f:
            f.write(r.content)
        with zipfile.ZipFile("corp.zip", "r") as z:
            z.extractall("corp")
        tree = ET.parse("corp/CORPCODE.xml")
        root = tree.getroot()
        for item in root.findall("list"):
            sc = item.findtext("stock_code", "").strip()
            cc = item.findtext("corp_code",  "").strip()
            if sc:
                CORP_MAP[sc] = cc
        with open(CORP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(CORP_MAP, f, ensure_ascii=False)
        print(f"[DART] corp_map 완료 ({len(CORP_MAP)}개)")
    except Exception as e:
        CORP_MAP = {}
        print(f"[DART][FATAL] corp_map 실패: {type(e).__name__}")

def get_financial(corp_code, year):
    try:
        r = requests.get(
            f"{DART_BASE}/fnlttSinglAcnt.json",
            params={
                "crtfc_key":  DART_API_KEY,
                "corp_code":  corp_code,
                "bsns_year":  str(year),
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
    r = {"roe": None, "debt_ratio": None, "net": 0, "equity": 0, "debt": 0}
    for i in fin_list:
        nm = i.get("account_nm", "")
        v  = safe_int(i.get("thstrm_amount", 0))
        if "당기순이익" in nm:
            r["net"]    = v
        elif "자본총계" in nm:
            r["equity"] = v
        elif "부채총계" in nm:
            r["debt"]   = v
    if r["equity"] > 0:
        r["roe"]        = round(min((r["net"] / r["equity"]) * 100, 50), 2)
        r["debt_ratio"] = round((r["debt"] / r["equity"]) * 100, 2)
    return r

def get_fin(stock_code):
    if stock_code in DART_CACHE:
        return DART_CACHE[stock_code]
    corp = CORP_MAP.get(stock_code)
    if not corp:
        DART_CACHE[stock_code] = None
        return None
    year = datetime.now().year
    for y in [year - 1, year - 2]:
        fin = get_financial(corp, y)
        if fin:
            result = extract(fin)
            DART_CACHE[stock_code] = result
            return result
    DART_CACHE[stock_code] = None
    return None

def calc_score(rank, fin):
    if not fin:
        return 0
    s   = 20 - (rank / 200 * 20)
    roe = fin.get("roe") or 0
    dr  = fin.get("debt_ratio") or 999
    s  += math.log1p(max(roe, 0)) * 10
    s  += max(0, 40 - dr * 0.2)
    if fin.get("net", 0) > 0:
        s += 20
    return round(s, 2)

def signal(sc, fin):
    if not fin:  return "KRX_ONLY"
    if sc >= 70: return "STRONG_BUY"
    if sc >= 55: return "BUY"
    if sc >= 40: return "HOLD"
    if sc >= 25: return "WATCH"
    return "PASS"

def main():
    print("[START] V9 FINAL ENGINE")

    load_corp_map()

    tickers = get_top200()
    if not tickers:
        print("[FATAL] NO MARKET DATA → STOP")
        raise Exception("EMPTY MARKET DATA - ABORT")

    results = []
    for rank, (code, name, mcap) in enumerate(tickers, 1):
        fin = get_fin(code)
        sc  = calc_score(rank, fin)
        sig = signal(sc, fin)
        results.append({
            "code":       code,
            "name":       name,
            "market_cap": mcap,
            "score":      sc,
            "signal":     sig,
            "roe":        fin["roe"]        if fin and fin.get("roe")        is not None else 0,
            "debt_ratio": fin["debt_ratio"] if fin and fin.get("debt_ratio") is not None else 0
        })
        time.sleep(0.1)

    results.sort(key=lambda x: x["score"], reverse=True)

    kst = get_kst()
    output = {
        "version":      datetime.now(kst).strftime("%Y%m%d_%H%M"),
        "generated_at": datetime.now(kst).isoformat(),
        "meta": {
            "krx_count":  len(tickers),
            "processed":  len(results),
            "corp_map":   len(CORP_MAP),
            "dart_cache": len(DART_CACHE)
        },
        "top10": results[:10],
        "all":   results
    }

    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    if len(output.get("all", [])) == 0:
        raise Exception("EMPTY RESULTS - ABORT")

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    shutil.move(RAW_PATH, OUTPUT_PATH)

    print(f"\n[DONE] {len(results)}개 저장 완료")
    print("\n[TOP10]")
    for i, r in enumerate(results[:10], 1):
        print(f"{i:2}. {r['name']} ({r['code']}) | {r['signal']} | {r['score']}점 | ROE {r['roe']}% | 부채 {r['debt_ratio']}%")

if __name__ == "__main__":
    main()
