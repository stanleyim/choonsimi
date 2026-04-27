import os, json, time, requests, threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

KRX_API_KEY = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

KRX_URL = "https://apis.data.go.kr/1160100/service/GetItemInfoService/getItemAll"
DART_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

OUTPUT_PATH = "data.json"

corp_code_cache = {}

# 🔥 글로벌 rate limiter
lock = threading.Lock()
last_call = 0

def rate_limit():
    global last_call
    with lock:
        now = time.time()
        wait = 0.25 - (now - last_call)   # 초당 4건
        if wait > 0:
            time.sleep(wait)
        last_call = time.time()

def safe_int(v):
    try: return int(str(v).replace(",", ""))
    except: return 0

def load_corp_codes():
    import zipfile, io, xml.etree.ElementTree as ET
    try:
        r = requests.get(f"{DART_CORP_URL}?crtfc_key={DART_API_KEY}", timeout=20)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read("CORPCODE.xml")
        root = ET.fromstring(xml)
        for corp in root.findall("list"):
            stock_code = corp.find("stock_code").text
            if stock_code and stock_code.strip():
                corp_code_cache[stock_code] = corp.find("corp_code").text
        print(f"[DART] corp loaded: {len(corp_code_cache)}")
    except Exception as e:
        print("[ERROR] corp load:", e)

def get_top200():
    try:
        params = {"market": "ALL", "apiKey": KRX_API_KEY, "resultType": "json"}
        r = requests.get(KRX_URL, params=params, timeout=20)
        stocks = r.json().get("output", [])

        cleaned = []
        for s in stocks:
            mkp = safe_int(s.get("mkp"))
            if mkp > 0:
                cleaned.append((s["srtnCd"], s["itmsNm"], mkp))

        cleaned.sort(key=lambda x: x[2], reverse=True)
        return [(c, n) for c, n, _ in cleaned[:200]]

    except Exception as e:
        print("[ERROR] KRX:", e)
        return []

def get_fin(corp_code, year):
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011"
    }

    for _ in range(3):
        try:
            rate_limit()  # 🔥 핵심
            r = requests.get(DART_FIN_URL, params=params, timeout=10)
            data = r.json()

            if data.get("status") == "000":
                return data.get("list", [])

            time.sleep(0.5)

        except:
            time.sleep(0.5)

    return []

def calc_score(fin, prev_rev=0):
    try:
        m = {f.get("account_nm"): safe_int(f.get("amount")) for f in fin}

        equity = m.get("자본총계", 0)
        if equity == 0: return 0

        roe = (m.get("당기순이익", 0) / equity) * 100
        debt = (m.get("부채총계", 0) / equity) * 100
        rev = m.get("매출액", 0)

        growth = ((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0

        score = (
            min(40, max(0, roe * 1.5)) +
            min(30, max(0, 30 - debt * 0.2)) +
            min(30, max(0, growth))
        )

        return round(score, 1)
    except:
        return 0

def process_stock(code, name, corp, year):
    try:
        fin = get_fin(corp, year)
        if not fin: return None

        prev = get_fin(corp, year - 1)
        prev_rev = next(
            (safe_int(x["amount"]) for x in prev if x.get("account_nm") == "매출액"),
            0
        )

        score = calc_score(fin, prev_rev)
        if score > 0:
            return {"code": code, "name": name, "score": score}

    except:
        return None

def main():
    print("[START] engine run")

    load_corp_codes()
    tickers = get_top200()
    if not tickers:
        return

    year = datetime.now().year - 1
    results = []

    # 🔥 제한된 병렬
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = []

        for code, name in tickers:
            corp = corp_code_cache.get(code)
            if corp:
                futures.append(ex.submit(process_stock, code, name, corp, year))

        for i, f in enumerate(as_completed(futures), 1):
            result = f.result()
            if result:
                results.append(result)

            if i % 20 == 0:
                print(f"[PROGRESS] {i}/{len(futures)}")

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "top10": results[:10],
        "all": results[:200]
    }

    if not output["top10"] and os.path.exists(OUTPUT_PATH):
        print("[WARN] keep previous data")
        return

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"[DONE] {len(output['top10'])} stocks")

if __name__ == "__main__":
    main()
