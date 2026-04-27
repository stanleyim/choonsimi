import os
import json
import time
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

KRX_API_KEY = os.getenv("KRX_API_KEY")
DART_API_KEY = os.getenv("DART_API_KEY")

KRX_URL = "https://apis.data.go.kr/1160100/service/GetItemInfoService/getItemAll"
DART_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

# 🔥 핵심 변경: 루트 저장
OUTPUT_PATH = "data.json"

HEADERS = {"User-Agent": "Mozilla/5.0"}
corp_code_cache = {}


# =========================
# 안전한 숫자 변환
# =========================
def safe_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0


# =========================
# DART corp_code 로드
# =========================
def load_corp_codes():
    import zipfile, io, xml.etree.ElementTree as ET

    try:
        url = f"{DART_CORP_URL}?crtfc_key={DART_API_KEY}"
        r = requests.get(url, timeout=20)

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
        stocks = r.json().get("output", [])

        cleaned = []
        for s in stocks:
            try:
                mkp = safe_int(s.get("mkp"))
                if mkp > 0:
                    cleaned.append((s["srtnCd"], s["itmsNm"], mkp))
            except:
                continue

        cleaned.sort(key=lambda x: x[2], reverse=True)
        return [(c, n) for c, n, _ in cleaned[:200]]

    except Exception as e:
        print("[ERROR] KRX:", e)
        return []


# =========================
# DART 재무
# =========================
def get_fin(corp_code, year):
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011"
    }

    for _ in range(3):
        try:
            r = requests.get(DART_FIN_URL, params=params, timeout=10)
            data = r.json()

            if data.get("status") == "000":
                return data.get("list", [])

            time.sleep(0.3)

        except:
            time.sleep(0.5)

    return []


# =========================
# 점수 계산
# =========================
def calc_score(fin, prev_rev=0):
    try:
        m = {}
        for f in fin:
            m[f.get("account_nm")] = safe_int(f.get("amount"))

        equity = m.get("자본총계", 0)
        if equity == 0:
            return 0

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


# =========================
# MAIN
# =========================
def main():
    print("[START] engine")

    load_corp_codes()

    tickers = get_top200()
    if not tickers:
        print("[STOP] no KRX data")
        return

    year = datetime.now().year - 1
    results = []

    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {}

        for code, name in tickers:
            corp = corp_code_cache.get(code)
            if corp:
                futures[ex.submit(get_fin, corp, year)] = (code, name, corp)

        for f in as_completed(futures):
            code, name, corp = futures[f]

            try:
                fin = f.result()
                if not fin:
                    continue

                prev = get_fin(corp, year - 1)

                prev_rev = next(
                    (safe_int(x["amount"]) for x in prev
                     if x.get("account_nm") == "매출액"),
                    0
                )

                score = calc_score(fin, prev_rev)

                if score > 0:
                    results.append({
                        "code": code,
                        "name": name,
                        "score": score
                    })

                time.sleep(0.08)

            except:
                continue

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "top10": results[:10],
        "all": results[:200]
    }

    # 🔥 fallback (빈 결과 방지)
    if not output["top10"] and os.path.exists(OUTPUT_PATH):
        print("[WARN] keep previous data")
        return

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"[DONE] {len(output['top10'])} items")


if __name__ == "__main__":
    main()
