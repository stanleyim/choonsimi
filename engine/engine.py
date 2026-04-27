import os
import json
import time
import requests
import threading
import shutil
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# CONFIG
# =========================
KRX_API_KEY = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

KRX_URL = "https://apis.data.go.kr/1160100/service/GetItemInfoService/getItemAll"
DART_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

corp_code_cache = {}

# =========================
# RATE LIMITER
# =========================
lock = threading.Lock()
last_call = 0

def rate_limit():
    global last_call
    with lock:
        now = time.time()
        wait = 0.28 - (now - last_call)  # 안정적 3~4 req/sec
        if wait > 0:
            time.sleep(wait)
        last_call = time.time()

# =========================
# UTIL
# =========================
def safe_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0

# =========================
# DART CORP CODE LOAD
# =========================
def load_corp_codes():
    import zipfile, io, xml.etree.ElementTree as ET

    try:
        r = requests.get(
            f"{DART_CORP_URL}?crtfc_key={DART_API_KEY}",
            timeout=20
        )
        r.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read("CORPCODE.xml")

        root = ET.fromstring(xml)

        for corp in root.findall("list"):
            stock_code = corp.find("stock_code").text
            corp_code = corp.find("corp_code").text

            if stock_code and stock_code.strip():
                corp_code_cache[stock_code] = corp_code

        print(f"[INFO] corp loaded: {len(corp_code_cache)}")

    except Exception as e:
        print(f"[ERROR] load_corp_codes: {e}")

# =========================
# KRX TOP 200
# =========================
def get_top200():
    try:
        params = {
            "market": "ALL",
            "apiKey": KRX_API_KEY,
            "resultType": "json"
        }

        r = requests.get(KRX_URL, params=params, timeout=20)
        r.raise_for_status()

        stocks = r.json().get("output", [])

        cleaned = []
        for s in stocks:
            mkp = safe_int(s.get("mkp"))
            if mkp > 0:
                cleaned.append((s["srtnCd"], s["itmsNm"], mkp))

        cleaned.sort(key=lambda x: x[2], reverse=True)

        return [(c, n) for c, n, _ in cleaned[:200]]

    except Exception as e:
        print(f"[ERROR] KRX: {e}")
        return []

# =========================
# DART FINANCE CALL
# =========================
def get_fin(corp, year):
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp,
        "bsns_year": str(year),
        "reprt_code": "11011"
    }

    for _ in range(3):
        try:
            rate_limit()
            r = requests.get(DART_FIN_URL, params=params, timeout=10)
            data = r.json()

            if data.get("status") == "000":
                return data.get("list", [])

            time.sleep(0.5)

        except:
            time.sleep(0.5)

    return []

# =========================
# SCORE ENGINE (핵심)
# =========================
def calc_score(fin, prev_rev=0):
    try:
        m = {f.get("account_nm"): safe_int(f.get("amount")) for f in fin}

        equity = m.get("자본총계", 0)
        if equity == 0:
            return 0, 0, "", 0

        roe = (m.get("당기순이익", 0) / equity) * 100
        debt = (m.get("부채총계", 0) / equity) * 100
        rev = m.get("매출액", 0)

        growth = ((rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0

        score = (
            min(40, max(0, roe * 1.5)) +
            min(30, max(0, 30 - debt * 0.2)) +
            min(30, max(0, growth))
        )

        # =========================
        # REASON ENGINE
        # =========================
        reason_parts = []

        if roe > 10:
            reason_parts.append(f"ROE {roe:.1f}%")

        if debt < 100:
            reason_parts.append(f"부채 안정 {debt:.0f}%")

        if growth > 5:
            reason_parts.append(f"매출 +{growth:.1f}%")

        reason = " · ".join(reason_parts[:3]) if reason_parts else "기본 재무 구조"

        # =========================
        # CONFIDENCE
        # =========================
        confidence = min(100, int(score + growth * 0.3))

        return round(score, 1), round(growth, 1), reason, confidence

    except:
        return 0, 0, "", 0

# =========================
# SIGNAL
# =========================
def get_signal(score):
    if score >= 70:
        return "BUY"
    elif score >= 50:
        return "WATCH"
    return "AVOID"

# =========================
# STOCK PROCESSOR
# =========================
def process_stock(code, name, corp, year):
    try:
        fin = get_fin(corp, year)
        if not fin:
            return None

        prev = get_fin(corp, year - 1)

        prev_rev = next(
            (safe_int(x.get("amount")) for x in prev
             if x.get("account_nm") == "매출액"),
            0
        )

        score, growth, reason, confidence = calc_score(fin, prev_rev)

        if score <= 0:
            return None

        return {
            "code": code,
            "name": name,
            "signal_strength": score,
            "signal": get_signal(score),
            "growth": growth,
            "reason": reason,
            "confidence": confidence
        }

    except Exception as e:
        print(f"[ERROR] {code}: {e}")
        return None

# =========================
# MAIN PIPELINE
# =========================
def main():
    print("[START] choonsimi engine")

    load_corp_codes()
    tickers = get_top200()

    if not tickers:
        print("[FAIL] no data")
        return

    year = datetime.now().year - 1
    results = []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [
            ex.submit(process_stock, c, n, corp_code_cache.get(c), year)
            for c, n in tickers
            if corp_code_cache.get(c)
        ]

        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r:
                results.append(r)

            if i % 20 == 0:
                print(f"[PROGRESS] {i}/{len(futures)}")

    results.sort(key=lambda x: x["signal_strength"], reverse=True)

    version = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M")

    output = {
        "version": version,
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "top10": results[:10],
        "all": results[:200]
    }

    # =========================
    # SAFETY BACKUP
    # =========================
    if not output["top10"]:
        print("[WARN] empty result - skip save")
        return

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] TOP10={len(output['top10'])} VERSION={version}")

if __name__ == "__main__":
    main()
