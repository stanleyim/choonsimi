‎‎import os, json, math, shutil, requests, time
‎from datetime import datetime, timedelta
‎
‎OUTPUT_PATH = "data.json"
‎BACKUP_PATH = "data.json.bak"
‎HISTORY_PATH = "history.csv"
‎CORP_MAP_PATH = "corp_map.json"
‎
‎KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
‎KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
‎KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
‎DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
‎
‎─────────────────────────────
‎SAFE PARSER
‎─────────────────────────────
‎def safe_int(v):
‎    try:
‎        return int(str(v).replace(",", "").strip())
‎    except:
‎        return 0
‎
‎def safe_float(v):
‎    try:
‎        return float(str(v).replace(",", "").strip())
‎    except:
‎        return 0.0
‎
‎─────────────────────────────
‎DATE (3 DAY FALLBACK)
‎─────────────────────────────
‎def get_dates():
‎    base = datetime.now()
‎    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
‎
‎─────────────────────────────
‎KRX CALL
‎─────────────────────────────
‎def call_krx(url, date):
‎    try:
‎        r = requests.get(
‎            url,
‎            params={"basDd": date},
‎            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
‎            timeout=5
‎        )
‎        j = r.json()
‎        return j.get("OutBlock_1") or j.get("block1") or []
‎    except:
‎        return []
‎
‎─────────────────────────────
‎DART FINANCIAL
‎─────────────────────────────
‎def get_dart_financial(corp_code, dart_key):
‎    if not dart_key or not corp_code:
‎        return 0
‎    try:
‎        params = {
‎            "crtfc_key": dart_key,
‎            "corp_code": corp_code,
‎            "bsns_year": str(datetime.now().year - 1),
‎            "reprt_code": "11011" # 사업보고서
‎        }
‎        r = requests.get(DART_URL, params=params, timeout=5)
‎        data = r.json()
‎
‎        if data.get("status") != "000":
‎            return 0
‎
‎        debt = equity = 0
‎        for item in data.get("list", []):
‎            if item.get("account_nm") == "부채총계":
‎                debt = safe_float(item.get("amount", 0))
‎            if item.get("account_nm") == "자본총계":
‎                equity = safe_float(item.get("amount", 0))
‎[4/29, 5:27 PM] Meta AI: 여기 최종 완성본이야. `corp_map.json`이 문자열이든 딕셔너리든 둘 다 대응하고, DART 에러 핸들링까지 다 들어간 버전이야 ✅
‎
‎import os, json, math, shutil, requests, time
‎from datetime import datetime, timedelta
‎
‎OUTPUT_PATH = "data.json"
‎BACKUP_PATH = "data.json.bak"
‎HISTORY_PATH = "history.csv"
‎CORP_MAP_PATH = "corp_map.json"
‎
‎KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
‎KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
‎KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
‎DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
‎
‎─────────────────────────────
‎SAFE PARSER
‎─────────────────────────────
‎def safe_int(v):
‎    try:
‎        return int(str(v).replace(",", "").strip())
‎    except:
‎        return 0
‎
‎def safe_float(v):
‎    try:
‎        return float(str(v).replace(",", "").strip())
‎    except:
‎        return 0.0
‎
‎─────────────────────────────
‎DATE (3 DAY FALLBACK)
‎─────────────────────────────
‎def get_dates():
‎    base = datetime.now()
‎    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
‎
‎─────────────────────────────
‎KRX CALL
‎─────────────────────────────
‎def call_krx(url, date):
‎    try:
‎        r = requests.get(
‎            url,
‎            params={"basDd": date},
‎            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
‎            timeout=5
‎        )
‎        j = r.json()
‎        return j.get("OutBlock_1") or j.get("block1") or []
‎    except:
‎        return []
‎
‎─────────────────────────────
‎DART FINANCIAL
‎─────────────────────────────
‎def get_dart_financial(corp_code, dart_key):
‎    if not dart_key or not corp_code:
‎        return 0
‎    try:
‎        params = {
‎            "crtfc_key": dart_key,
‎            "corp_code": corp_code,
‎            "bsns_year": str(datetime.now().year - 1),
‎            "reprt_code": "11011" # 사업보고서
‎        }
‎        r = requests.get(DART_URL, params=params, timeout=5)
‎        data = r.json()
‎
‎        if data.get("status") != "000":
‎            return 0
‎
‎        debt = equity = 0
‎        for item in data.get("list", []):
‎            if item.get("account_nm") == "부채총계":
‎                debt = safe_float(item.get("amount", 0))
‎            if item.get("account_nm") == "자본총계":
‎                equity = safe_float(item.get("amount", 0))
‎
‎        if equity <= 0:
‎            return 0
‎        debt_ratio = (debt / equity) * 100
‎        # 부채비율 100% 이하=10점, 200%=0점 선형 보정
‎        return max(0, 10 - (debt_ratio / 20))
‎    except Exception as e:
‎        print(f"[DART ERROR] {corp_code}: {e}")
‎        return 0
‎
‎─────────────────────────────
‎#
‎[4/29, 5:27 PM] Meta AI: 여기 최종 완성본이야. `corp_map.json`이 문자열이든 딕셔너리든 둘 다 대응하고, DART 에러 핸들링까지 다 들어간 버전이야 ✅
‎
‎import os, json, math, shutil, requests, time
‎from datetime import datetime, timedelta
‎
‎OUTPUT_PATH = "data.json"
‎BACKUP_PATH = "data.json.bak"
‎HISTORY_PATH = "history.csv"
‎CORP_MAP_PATH = "corp_map.json"
‎
‎KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
‎KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
‎KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
‎DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
‎
‎─────────────────────────────
‎SAFE PARSER
‎─────────────────────────────
‎def safe_int(v):
‎    try:
‎        return int(str(v).replace(",", "").strip())
‎    except:
‎        return 0
‎
‎def safe_float(v):
‎    try:
‎        return float(str(v).replace(",", "").strip())
‎    except:
‎        return 0.0
‎
‎─────────────────────────────
‎DATE (3 DAY FALLBACK)
‎─────────────────────────────
‎def get_dates():
‎    base = datetime.now()
‎    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
‎
‎─────────────────────────────
‎KRX CALL
‎─────────────────────────────
‎def call_krx(url, date):
‎    try:
‎        r = requests.get(
‎            url,
‎            params={"basDd": date},
‎            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
‎            timeout=5
‎        )
‎        j = r.json()
‎        return j.get("OutBlock_1") or j.get("block1") or []
‎    except:
‎        return []
‎
‎─────────────────────────────
‎DART FINANCIAL
‎─────────────────────────────
‎def get_dart_financial(corp_code, dart_key):
‎    if not dart_key or not corp_code:
‎        return 0
‎    try:
‎        params = {
‎            "crtfc_key": dart_key,
‎            "corp_code": corp_code,
‎            "bsns_year": str(datetime.now().year - 1),
‎            "reprt_code": "11011" # 사업보고서
‎        }
‎        r = requests.get(DART_URL, params=params, timeout=5)
‎        data = r.json()
‎
‎        if data.get("status") != "000":
‎            return 0
‎
‎        debt = equity = 0
‎        for item in data.get("list", []):
‎            if item.get("account_nm") == "부채총계":
‎                debt = safe_float(item.get("amount", 0))
‎            if item.get("account_nm") == "자본총계":
‎                equity = safe_float(item.get("amount", 0))
‎
‎        if equity <= 0:
‎            return 0
‎        debt_ratio = (debt / equity) * 100
‎        # 부채비율 100% 이하=10점, 200%=0점 선형 보정
‎        return max(0, 10 - (debt_ratio / 20))
‎    except Exception as e:
‎        print(f"[DART ERROR] {corp_code}: {e}")
‎        return 0
‎
‎─────────────────────────────
‎LOAD MARKET DATA
‎─────────────────────────────
‎def load_market():
‎    for d in get_dates():
‎        kospi = call_krx(KOSPI_URL, d)
‎        kosdaq = call_krx(KOSDAQ_URL, d)
‎        data = kospi + kosdaq
‎        if len(data) > 0:
‎            return data, d
‎    return [], None
‎
‎─────────────────────────────
‎UNIVERSE (TOP 200 MKT CAP
‎[4/29, 5:27 PM] Meta AI: 여기 최종 완성본이야. `corp_map.json`이 문자열이든 딕셔너리든 둘 다 대응하고, DART 에러 핸들링까지 다 들어간 버전이야 ✅
‎
‎import os, json, math, shutil, requests, time
‎from datetime import datetime, timedelta
‎
‎OUTPUT_PATH = "data.json"
‎BACKUP_PATH = "data.json.bak"
‎HISTORY_PATH = "history.csv"
‎CORP_MAP_PATH = "corp_map.json"
‎
‎KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
‎KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
‎KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
‎DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
‎
‎─────────────────────────────
‎SAFE PARSER
‎─────────────────────────────
‎def safe_int(v):
‎    try:
‎        return int(str(v).replace(",", "").strip())
‎    except:
‎        return 0
‎
‎def safe_float(v):
‎    try:
‎        return float(str(v).replace(",", "").strip())
‎    except:
‎        return 0.0
‎
‎─────────────────────────────
‎DATE (3 DAY FALLBACK)
‎─────────────────────────────
‎def get_dates():
‎    base = datetime.now()
‎    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
‎
‎─────────────────────────────
‎KRX CALL
‎─────────────────────────────
‎def call_krx(url, date):
‎    try:
‎        r = requests.get(
‎            url,
‎            params={"basDd": date},
‎            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
‎            timeout=5
‎        )
‎        j = r.json()
‎        return j.get("OutBlock_1") or j.get("block1") or []
‎    except:
‎        return []
‎
‎─────────────────────────────
‎DART FINANCIAL
‎─────────────────────────────
‎def get_dart_financial(corp_code, dart_key):
‎    if not dart_key or not corp_code:
‎        return 0
‎    try:
‎        params = {
‎            "crtfc_key": dart_key,
‎            "corp_code": corp_code,
‎            "bsns_year": str(datetime.now().year - 1),
‎            "reprt_code": "11011" # 사업보고서
‎        }
‎        r = requests.get(DART_URL, params=params, timeout=5)
‎        data = r.json()
‎
‎        if data.get("status")!= "000":
‎            return 0
‎
‎        debt = equity = 0
‎        for item in data.get("list", []):
‎            if item.get("account_nm") == "부채총계":
‎                debt = safe_float(item.get("amount", 0))
‎            if item.get("account_nm") == "자본총계":
‎                equity = safe_float(item.get("amount", 0))
‎
‎        if equity <= 0:
‎            return 0
‎        debt_ratio = (debt / equity) * 100
‎        # 부채비율 100% 이하=10점, 200%=0점 선형 보정
‎        return max(0, 10 - (debt_ratio / 20))
‎    except Exception as e:
‎        print(f"[DART ERROR] {corp_code}: {e}")
‎        return 0
‎
‎─────────────────────────────
‎LOAD MARKET DATA
‎─────────────────────────────
‎def load_market():
‎    for d in get_dates():
‎        kospi = call_krx(KOSPI_URL, d)
‎        kosdaq = call_krx(KOSDAQ_URL, d)
‎        data = kospi + kosdaq
‎        if len(data) > 0:
‎            return data, d
‎    return [], None
‎
‎─────────────────────────────
‎UNIVERSE (TOP 200 MKT CAP)
‎─────────────────────────────
‎def get_universe(items):
‎    cleaned = []
‎    for s in items:
‎        code = s.get("ISU_CD")
‎        mcap = safe_int(s.get("MKTCAP", 0))
‎        if code and mcap > 0:
‎            cleaned.append((code, mcap))
‎    cleaned.sort(key=lambda x: x[1], reverse=True)
‎    return [c[0] for c in cleaned[:200]]
‎
‎─────────────────────────────
‎FEATURES
‎─────────────────────────────
‎def features(s):
‎    close = safe_int(s.get("TDD_CLSPRC", 0))
‎    vol = safe_int(s.get("ACC_TRDVOL", 0))
‎    momentum = math.log1p(close)
‎    liquidity = math.log1p(vol)
‎    risk = 1 / (1 + math.log1p(abs(vol) + 1))
‎    return momentum, liquidity, risk, close
‎
‎─────────────────────────────
‎SCORE ENGINE
‎─────────────────────────────
‎def score(m, l, r, rank, dart_score=0):
‎    size = 20 - (rank / 200 * 20)
‎    base_score = size * 0.2 + m * 0.4 + l * 0.2 + r * 0.2
‎    # DART 30% 가중치 적용
‎    return base_score * 0.7 +
‎[4/29, 5:45 PM] Meta AI: 아 문법이 깨져있어. 따옴표랑 `isinstance` 위치가 잘못됐어 ❌
‎
‎*잘못된 부분*
‎corp_code_raw = corp_map.get(code, ")
‎corp_code = corp_code_raw if isinstance .get(corp_code_raw, str)
‎else corp_code_raw.get("corp_code", ")
‎
‎
‎*올바른 부분*
‎corp_code_raw = corp_map.get(code, "")
‎corp_code = corp_code_raw if isinstance(corp_code_raw, str) else corp_code_raw.get("corp_code", "")
‎
‎
‎*수정 포인트 3개:*
‎1. `""` 따옴표 닫기 - `")` → `""`
‎2. `isinstance(corp_code_raw, str)` - 띄어쓰기 없이 붙이기
‎3. `else` 한줄로 붙이기 - 줄바꿈하면 SyntaxError 나
‎
‎이 2줄만 다시 붙여넣으면 돼
‎[4/29, 5:54 PM] Meta AI: 완성본 전체 그대로 붙여넣어. 코드박스 없이 일반 텍스트로 줘:
‎
‎import os, json, math, shutil, requests, time
‎from datetime import datetime, timedelta
‎
‎OUTPUT_PATH = "data.json"
‎BACKUP_PATH = "data.json.bak"
‎HISTORY_PATH = "history.csv"
‎CORP_MAP_PATH = "corp_map.json"
‎
‎KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
‎KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
‎KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
‎DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
‎
‎SAFE PARSER
‎def safe_int(v):
‎    try:
‎        return int(str(v).replace(",", "").strip())
‎    except:
‎        return 0
‎
‎def safe_float(v):
‎    try:
‎        return float(str(v).replace(",", "").strip())
‎    except:
‎        return 0.0
‎
‎DATE 3 DAY FALLBACK
‎def get_dates():
‎    base = http://datetime.now()
‎    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
‎
‎KRX CALL
‎def call_krx(url, date):
‎    try:
‎        r = http://requests.get(
‎            url,
‎            params={"basDd": date},
‎            headers={"AUTH_KEY": http://os.getenv("KRX_API_KEY")},
‎            timeout=5
‎        )
‎        j = http://r.json()
‎        return http://j.get("OutBlock_1") or http://j.get("block1") or []
‎    except:
‎        return []
‎
‎DART FINANCIAL
‎def get_dart_financial(corp_code, dart_key):
‎    if not dart_key or not corp_code:
‎        return 0
‎    try:
‎        params = {
‎            "crtfc_key": dart_key,
‎            "corp_code": corp_code,
‎            "bsns_year": str(datetime.now().year - 1), 
‎            "reprt_code": "11011"
‎        }
‎        r = http://requests.get(DART_URL, params=params, timeout=5)
‎        data = http://r.json()
‎        
‎        if http://data.get("status")!= "000":
‎            return 0
‎            
‎        debt = equity = 0
‎        for item in http://data.get("list", []):
‎            if http://item.get("account_nm") == "부채총계":
‎                debt = safe_float(item.get("amount", 0))
‎            if http://item.get("account_nm") == "자본총계": 
‎                equity = safe_float(item.get("amount", 0))
‎                
‎        if equity <= 0:
‎            return 0
‎        debt_ratio = (debt / equity) _ 100
‎        return max(0, 10 - (debt_ratio / 20))
‎    except Exception as e:
‎        print(f"[DART ERROR] {corp_code}: {e}")
‎        return 0
‎
‎LOAD MARKET DATA
‎def load_market():
‎    for d in get_dates():
‎        kospi = call_krx(KOSPI_URL, d)
‎        kosdaq = call_krx(KOSDAQ_URL, d)
‎        data = kospi + kosdaq
‎        if len(data) > 0:
‎            return data, d
‎    return [], None
‎
‎UNIVERSE TOP 200 MKT CAP
‎def get_universe(items):
‎    cleaned = []
‎    for s in items:
‎        code = http://s.get("ISU_CD")
‎        mcap = safe_int(s.get("MKTCAP", 0))
‎        if code and mcap > 0:
‎            http://cleaned.append((code, mcap))
‎    http://cleaned.sort(key=lambda x: x, reverse=True)
‎    return [c for c in cleaned[:200]]
‎
‎FEATURES
‎def features(s):
‎    close = safe_int(s.get("TDD_CLSPRC", 0))
‎    vol = safe_int(s.get("ACC_TRDVOL", 0))
‎    momentum = http://math.log1p(close)
‎    liquidity = http://math.log1p(vol)
‎    risk = 1 / (1 + http://math.log1p(abs(vol) + 1))
‎    return momentum, liquidity, risk, close
‎
‎SCORE ENGINE
‎def score(m, l, r, rank, dart_score=0):
‎    size = 20 - (rank / 200 _ 20)
‎    base_score = size _ 0.2 + m _ 0.4 + l _ 0.2 + r _ 0.2
‎    return base_score _ 0.7 + dart_score _ 0.3
‎
‎MAIN
‎def main():
‎    print("[ENGINE v7.5 DART START]")
‎    
‎    corp_map = {}
‎    if http://os.path.exists(CORP_MAP_PATH):
‎        with open(CORP_MAP_PATH, 'r', encoding='utf-8') as f:
‎            corp_map = http://json.load(f)
‎    else:
‎        print("[WARN] corp_map.json not found. DART score will be 0")
‎    
‎    dart_key = http://os.getenv("DART_API_KEY")
‎    market, used_date = load_market()
‎
‎    if len(market) < 50:
‎        print("[SKIP] insufficient market data")
‎        return
‎
‎    universe = get_universe(market)
‎    results = []
‎
‎    for i, code in enumerate(universe, 1):
‎        s = next((x for x in market if http://x.get("ISU_CD") == code), None)
‎        if not s:
‎            continue
‎
‎        m, l, r, close = features(s)
‎        
‎        # corp_code 매핑 문자열 딕셔너리 둘 다 대응
‎        corp_code_raw = corp_map.get(code, "")
‎        corp_code = corp_code_raw if isinstance(corp_code_raw, str) else corp_code_raw.get("corp_code", "")
‎        
‎        # DART 점수 계산
‎        dart_score = get_dart_financial(corp_code, dart_key)
‎        http://time.sleep(0.2)
‎        
‎        sc = score(m, l, r, i, dart_score)
‎
‎        http://results.append({
‎            "code": code,
‎            "score": round(sc, 4),
‎            "dart_score": round(dart_score, 4),
‎            "close": close
‎        })
‎        
‎        http://time.sleep(0.3)
‎        if i % 20 == 0:
‎            print(f"[{i}/200] 처리중...")
‎
‎    http://results.sort(key=lambda x: x["score"], reverse=True)
‎
‎    output = {
‎        "time": http://datetime.now().isoformat(),
‎        "data_date": used_date,
‎        "mode": "v7.5_dart",
‎        "top10": results[:10],
‎        "all": results
‎    }
‎
‎    if http://os.path.exists(OUTPUT_PATH):
‎        http://shutil.copy(OUTPUT_PATH, BACKUP_PATH)
‎
‎    with open(OUTPUT_PATH, "w", encoding='utf-8') as f:
‎        http://json.dump(output, f, indent=2, ensure_ascii=False)
‎
‎    with open(HISTORY_PATH, "a", encoding='utf-8') as f:
‎        for item in results:
‎            http://f.write(f'{item["code"]},{used_date},{item["close"]},{item["score"]},{item["dart_score"]}\n')
‎
‎    print(f"[DONE] {len(results)} stocks + history saved. Runtime ∼100s")
‎
‎if *name* == "*main*":
‎    main()
‎
