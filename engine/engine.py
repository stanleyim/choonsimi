import os, json, math, shutil, requests, time
from datetime import datetime, timedelta

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"
HISTORY_PATH = "history.csv"
CORP_MAP_PATH = "corp_map.json"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"

DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

dart_cache = {}

# ---------------- SAFE UTILS ----------------
def safe_int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except:
        return 0

def safe_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except:
        return 0.0

def get_dates():
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]


# ---------------- KRX ----------------
def call_krx(url, date):
    try:
        r = requests.get(
            url,
            params={"basDd": date},
            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
            timeout=7
        )
        j = r.json()

        data = j.get("OutBlock_1") or j.get("block1") or []

        return data

    except Exception as e:
        print("[KRX ERROR]", e)
        return []


def load_market():
    for d in get_dates():
        kospi = call_krx(KOSPI_URL, d)
        kosdaq = call_krx(KOSDAQ_URL, d)

        data = kospi + kosdaq

        print(f"[KRX] date={d}, size={len(data)}")

        if len(data) > 50:
            return data, d

    return [], None


# ---------------- DART ----------------
def get_dart_financial(corp_code, dart_key):

    if not dart_key:
        return 0

    if not corp_code:
        return 0

    if corp_code in dart_cache:
        return dart_cache[corp_code]

    try:
        params = {
            "crtfc_key": dart_key,
            "corp_code": corp_code,
            "bsns_year": str(datetime.now().year - 1),
        }

        data = None

        for rpt in ["11013", "11012", "11014", "11011"]:
            params["reprt_code"] = rpt

            r = requests.get(DART_URL, params=params, timeout=7)
            j = r.json()

            if j.get("status") == "000" and j.get("list"):
                data = j
                break

        if not data:
            return 0

        op_income = revenue = 0
        debt = equity = 0
        current_assets = current_liab = 0

        for item in data.get("list", []):
            acc = item.get("account_nm", "")
            val = safe_float(item.get("thstrm_amount", 0))

            if "영업이익" in acc:
                op_income = val
            if "매출" in acc:
                revenue = val
            if "부채" in acc:
                debt = val
            if "자본" in acc:
                equity = val
            if "유동자산" in acc:
                current_assets = val
            if "유동부채" in acc:
                current_liab = val

        op_margin = (op_income / revenue * 100) if revenue > 0 else 0
        op_score = min(30, max(0, op_margin * 1.2))

        debt_score = 10 - min(10, (debt / equity * 100) / 40) if equity > 0 else 0

        curr_ratio = (current_assets / current_liab * 100) if current_liab > 0 else 0
        curr_score = min(20, curr_ratio / 20)

        dart_score = round(op_score + debt_score + curr_score, 2)

        dart_cache[corp_code] = dart_score

        return dart_score

    except Exception as e:
        print("[DART ERROR]", corp_code, e)
        return 0


# ---------------- FEATURES ----------------
def features(s):
    close = safe_int(s.get("TDD_CLSPRC", 0))
    vol = safe_int(s.get("ACC_TRDVOL", 0))

    if close == 0:
        return 0, 0, 0, 0

    momentum = math.log1p(close)
    liquidity = math.log1p(vol)
    risk = 1 / (1 + math.log1p(vol + 1))

    return momentum, liquidity, risk, close


# ---------------- SCORE ----------------
def score(m, l, r, rank, dart_score):

    size = 20 - (rank / 200 * 20)

    base = size * 0.2 + m * 0.35 + l * 0.25 + r * 0.2

    final = base * 0.75 + dart_score * 1.2

    return min(100, final)


# ---------------- MAIN ----------------
def main():

    print("[ENGINE v8.6 STABLE START]")

    dart_key = os.getenv("DART_API_KEY")

    print("[CHECK] DART KEY:", bool(dart_key))

    if not os.path.exists(CORP_MAP_PATH):
        raise RuntimeError("corp_map.json missing")

    with open(CORP_MAP_PATH, "r", encoding="utf-8") as f:
        corp_map = json.load(f)

    market, used_date = load_market()

    if len(market) == 0:
        raise RuntimeError("KRX FAILED: empty market data")

    print("[MARKET SIZE]", len(market))

    market_map = {s.get("ISU_CD"): s for s in market}

    universe = list(market_map.keys())[:200]

    results = []

    for i, code in enumerate(universe, 1):

        s = market_map.get(code)
        if not s:
            continue

        m, l, r, close = features(s)

        corp_info = corp_map.get(code)

        if not corp_info:
            continue

        corp_code = corp_info.get("corp_code")
        name = corp_info.get("name", code)

        if not corp_code:
            continue

        dart_score = get_dart_financial(corp_code, dart_key)

        sc = score(m, l, r, i, dart_score)

        results.append({
            "code": code,
            "name": name,
            "score": round(sc, 4),
            "dart_score": dart_score,
            "close": close
        })

        if i % 20 == 0:
            print(f"[{i}] dart={dart_score}")

        time.sleep(0.03)

    if len(results) == 0:
        raise RuntimeError("NO RESULTS GENERATED")

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "data_date": used_date,
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        for r in results:
            f.write(f'{r["code"]},{used_date},{r["close"]},{r["score"]},{r["dart_score"]}\n')

    print("[DONE] TOP1 DART:", results[0]["dart_score"])


if __name__ == "__main__":
    main()
