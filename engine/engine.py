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

def safe_int(v):
    try: return int(str(v).replace(",", "").strip())
    except: return 0

def safe_float(v):
    try: return float(str(v).replace(",", "").strip())
    except: return 0.0

def get_dates():
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]

def call_krx(url, date):
    try:
        r = requests.get(url, params={"basDd": date}, headers={"AUTH_KEY": os.getenv("KRX_API_KEY")}, timeout=5)
        j = r.json()
        return j.get("OutBlock_1") or j.get("block1") or []
    except:
        return []

def get_dart_financial(corp_code, dart_key):
    if not dart_key or not corp_code:
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
            r = requests.get(DART_URL, params=params, timeout=5)
            j = r.json()

            if j.get("status") == "000" and j.get("list"):
                data = j
                break

        if not data:
            dart_cache[corp_code] = 0
            return 0

        op_income = revenue = 0
        debt = equity = 0
        current_assets = current_liab = 0

        for item in data.get("list", []):
            acc = item.get("account_nm", "")
            val = safe_float(item.get("thstrm_amount", 0))

            # 독립 if (중요)
            if "영업이익" in acc:
                op_income = val

            if "매출" in acc or "수익" in acc:
                revenue = val

            if "부채총계" in acc:
                debt = val

            if "자본총계" in acc:
                equity = val

            if "유동자산" in acc:
                current_assets = val

            if "유동부채" in acc:
                current_liab = val

        # ---------- 점수 계산 ----------

        # 1. 영업이익률
        op_margin = (op_income / revenue * 100) if revenue > 0 else 0
        op_score = min(30, max(0, op_margin * 1.5))

        # 2. 부채비율
        if equity > 0:
            debt_ratio = (debt / equity) * 100
            debt_score = max(0, min(10, 10 - (debt_ratio / 30)))
        else:
            debt_score = 0

        # 3. 유동비율
        if current_liab > 0:
            current_ratio = (current_assets / current_liab) * 100
            curr_score = min(20, max(0, current_ratio / 15))
        else:
            curr_score = 0

        dart_score = op_score + debt_score + curr_score
        dart_score = round(dart_score, 2)

        dart_cache[corp_code] = dart_score
        return dart_score

    except Exception as e:
        print(f"[DART ERROR] {corp_code}: {e}")
        return 0

def load_market():
    for d in get_dates():
        kospi = call_krx(KOSPI_URL, d)
        kosdaq = call_krx(KOSDAQ_URL, d)
        data = kospi + kosdaq
        if len(data) > 0:
            return data, d
    return [], None

def get_universe(items):
    cleaned = []
    for s in items:
        code = s.get("ISU_CD")
        mcap = safe_int(s.get("MKTCAP", 0))
        if code and mcap > 0:
            cleaned.append((code, mcap))
    cleaned.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in cleaned[:200]]

def features(s):
    close = safe_int(s.get("TDD_CLSPRC", 0))
    vol = safe_int(s.get("ACC_TRDVOL", 0))
    momentum = math.log1p(close)
    liquidity = math.log1p(vol)
    risk = 1 / (1 + math.log1p(abs(vol) + 1))
    return momentum, liquidity, risk, close

def score(m, l, r, rank, dart_score=0):
    size = 20 - (rank / 200 * 20)
    base_score = size * 0.2 + m * 0.4 + l * 0.2 + r * 0.2
    return min(100, base_score * 0.7 + dart_score * 2)

def main():
    print("[ENGINE v8.0 FINAL]")

    with open(CORP_MAP_PATH, 'r', encoding='utf-8') as f:
        corp_map = json.load(f)

    dart_key = os.getenv("DART_API_KEY")
    market, used_date = load_market()

    if len(market) < 50:
        print("market load fail")
        return

    universe = get_universe(market)
    results = []

    for i, code in enumerate(universe, 1):
        s = next((x for x in market if x.get("ISU_CD") == code), None)
        if not s:
            continue

        m, l, r, close = features(s)

        corp_info = corp_map.get(code, {})
        corp_code = corp_info.get("corp_code", "")
        name = corp_info.get("name", code)

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
            print(f"[{i}/200] dart:{dart_score:.2f}")

        time.sleep(0.08)

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "data_date": used_date,
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w", encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    with open(HISTORY_PATH, "a", encoding='utf-8') as f:
        for item in results:
            f.write(f'{item["code"]},{used_date},{item["close"]},{item["score"]},{item["dart_score"]}\n')

    print(f"[DONE] Top1 DART: {results[0]['dart_score']:.2f}")

if __name__ == "__main__":
    main()
