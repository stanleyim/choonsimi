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
    except: return []

def get_dart_financial(corp_code, dart_key):
    if not dart_key or not corp_code: return 0
    try:
        params = {
            "crtfc_key": dart_key,
            "corp_code": corp_code,
            "bsns_year": str(datetime.now().year - 1),
            "reprt_code": "11011"
        }
        r = requests.get(DART_URL, params=params, timeout=5)
        data = r.json()
        if data.get("status")!= "000": return 0
        debt = equity = 0
        for item in data.get("list", []):
            if item.get("account_nm") == "부채총계": debt = safe_float(item.get("amount", 0))
            if item.get("account_nm") == "자본총계": equity = safe_float(item.get("amount", 0))
        if equity <= 0: return 0
        debt_ratio = (debt / equity) * 100
        return max(0, 10 - (debt_ratio / 20)) # 0~10점
    except Exception as e:
        print(f"[DART ERROR] {corp_code}: {e}")
        return 0

def load_market():
    for d in get_dates():
        kospi = call_krx(KOSPI_URL, d)
        kosdaq = call_krx(KOSDAQ_URL, d)
        data = kospi + kosdaq
        if len(data) > 0: return data, d
    return [], None

def get_universe(items):
    cleaned = []
    for s in items:
        code = s.get("ISU_CD")
        mcap = safe_int(s.get("MKTCAP", 0))
        if code and mcap > 0: cleaned.append((code, mcap))
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
    return min(100, base_score * 0.6 + dart_score * 4) # DART 가중치 4배

def main():
    print("[ENGINE v7.6 FINAL START]")

    corp_map = {}
    if os.path.exists(CORP_MAP_PATH):
        with open(CORP_MAP_PATH, 'r', encoding='utf-8') as f:
            corp_map = json.load(f)
        print(f"[DEBUG] corp_map loaded: {len(corp_map)} items")
    else:
        print(" corp_map.json not found. DART score will be 0")

    dart_key = os.getenv("DART_API_KEY")
    market, used_date = load_market()

    if len(market) < 50:
        print(" insufficient market data")
        return

    universe = get_universe(market)
    results = []

    for i, code in enumerate(universe, 1):
        s = next((x for x in market if x.get("ISU_CD") == code), None)
        if not s: continue

        m, l, r, close = features(s)

        # corp_code + name 매핑
        corp_info = corp_map.get(code, {})
        if isinstance(corp_info, str):
            corp_code = corp_info
            name = code
        else:
            corp_code = corp_info.get("corp_code", "")
            name = corp_info.get("name", code)

        # DART 점수 계산
        dart_score = get_dart_financial(corp_code, dart_key) if corp_code else 0
        time.sleep(0.2)

        sc = score(m, l, r, i, dart_score)

        results.append({
            "code": code,
            "ticker": code,
            "name": name,
            "score": round(sc, 4),
            "dart_score": round(dart_score, 4),
            "close": close,
            "volume": 0, "foreign": 0, "inst": 0, "tech": 70, "news": 0, "risk": 30
        })

        time.sleep(0.3)
        if i % 20 == 0:
            print(f"[{i}/200] 처리중... dart:{dart_score:.1f}")

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "data_date": used_date,
        "mode": "v7.6_final",
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

    print(f"[DONE] {len(results)} stocks saved. Top1 DART:{results[0]['dart_score']:.1f}")

if __name__ == "__main__":
    main()
