import os, json, math, shutil, requests, time, csv
from datetime import datetime, timedelta
from collections import defaultdict

# ─────────────────────────────────────────────
# PATHS (절대경로 — 어디서 실행해도 동일)
# ─────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data.json")
BACKUP_PATH = os.path.join(BASE_DIR, "..", "data.json.bak")
HISTORY_PATH= os.path.join(BASE_DIR, "..", "history.csv")
CORP_MAP_PATH = os.path.join(BASE_DIR, "corp_map.json")

# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────
KRX_BASE   = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL  = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"
DART_URL   = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

dart_cache = {}

# ─────────────────────────────────────────────
# SCORE WEIGHTS  (합계 = 1.0)
# ─────────────────────────────────────────────
W_RSI      = 0.25   # RSI 기술적 신호
W_VOLUME   = 0.20   # 거래량 서지
W_DART     = 0.25   # DART 재무 건전성
W_RELATIVE = 0.15   # 시장 상대강도
W_MOMENTUM = 0.15   # 가격 모멘텀

# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────
def safe_int(v):
    try:    return int(str(v).replace(",","").strip())
    except: return 0

def safe_float(v):
    try:    return float(str(v).replace(",","").strip())
    except: return 0.0

def get_dates():
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]

# ─────────────────────────────────────────────
# KRX
# ─────────────────────────────────────────────
def call_krx(url, date):
    try:
        r = requests.get(
            url,
            params={"basDd": date},
            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
            timeout=10
        )
        j = r.json()
        return j.get("OutBlock_1") or j.get("block1") or []
    except Exception as e:
        print(f"[KRX ERROR] {e}")
        return []

def load_market():
    for d in get_dates():
        kospi  = call_krx(KOSPI_URL,  d)
        kosdaq = call_krx(KOSDAQ_URL, d)
        data   = kospi + kosdaq
        print(f"[KRX] date={d}, size={len(data)}")
        if len(data) > 50:
            return data, d
    return [], None

# ─────────────────────────────────────────────
# HISTORY  (history.csv 읽어서 RSI / 모멘텀 계산)
# format: code,date,close,volume,score,dart_score
# ─────────────────────────────────────────────
def load_history():
    """code → [(date, close, volume), ...]  최신순 정렬"""
    h = defaultdict(list)
    if not os.path.exists(HISTORY_PATH):
        return h
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 4:
                    continue
                code, date, close = row[0], row[1], safe_float(row[2])
                vol = safe_float(row[3]) if len(row) > 3 else 0
                if close > 0:
                    h[code].append((date, close, vol))
    except:
        pass
    # 날짜 오름차순 → 최신이 마지막
    for code in h:
        h[code] = sorted(set(h[code]), key=lambda x: x[0])
    return h

def calc_rsi(prices, period=14):
    """prices: 오름차순 close 리스트"""
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # EMA
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - (100 / (1 + rs))

def rsi_score(rsi):
    """RSI → 0~100점"""
    if rsi is None:
        return 50  # 데이터 없으면 중립
    if rsi < 25:   return 95   # 강한 과매도 → 매수 기회
    if rsi < 35:   return 80
    if rsi < 45:   return 65
    if rsi < 55:   return 50   # 중립
    if rsi < 65:   return 40
    if rsi < 75:   return 25
    return 10                  # 강한 과매수 → 위험

def momentum_score(prices, days=20):
    """20일 수익률 → 0~100점"""
    if len(prices) < days + 1:
        return 50
    ret = (prices[-1] / prices[-days] - 1) * 100
    if ret >  15: return 95
    if ret >   8: return 80
    if ret >   3: return 65
    if ret >   0: return 55
    if ret >  -3: return 45
    if ret >  -8: return 30
    if ret > -15: return 15
    return 5

def volume_surge_score(today_vol, hist_vols):
    """오늘 거래량 / 20일 평균 → 0~100점"""
    if not hist_vols or today_vol == 0:
        return 50
    avg = sum(hist_vols[-20:]) / len(hist_vols[-20:])
    if avg == 0:
        return 50
    ratio = today_vol / avg
    if ratio > 5:   return 98
    if ratio > 3:   return 88
    if ratio > 2:   return 78
    if ratio > 1.5: return 65
    if ratio > 1.0: return 55
    if ratio > 0.7: return 45
    return 30

# ─────────────────────────────────────────────
# DART
# ─────────────────────────────────────────────
def get_dart_financial(corp_code, dart_key):
    if not dart_key or not corp_code:
        return 50  # 데이터 없으면 중립

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
            r = requests.get(DART_URL, params=params, timeout=8)
            j = r.json()
            if j.get("status") == "000" and j.get("list"):
                data = j
                break

        if not data:
            dart_cache[corp_code] = 50
            return 50

        op_income = revenue = debt = equity = cur_a = cur_l = 0

        for item in data.get("list", []):
            acc = item.get("account_nm", "")
            val = safe_float(item.get("thstrm_amount", 0))
            if "영업이익" in acc: op_income = val
            if "매출"   in acc: revenue   = val
            if "부채총계" in acc: debt     = val
            if "자본총계" in acc: equity   = val
            if "유동자산" in acc: cur_a    = val
            if "유동부채" in acc: cur_l    = val

        score = 0

        # ① 영업이익률 (0~40점)
        if revenue > 0:
            op_margin = op_income / revenue * 100
            score += min(40, max(0, op_margin * 1.5))

        # ② 부채비율 (0~30점)  낮을수록 좋음
        if equity > 0:
            debt_ratio = debt / equity * 100
            score += max(0, 30 - debt_ratio * 0.1)

        # ③ 유동비율 (0~30점)  높을수록 좋음
        if cur_l > 0:
            curr_ratio = cur_a / cur_l * 100
            score += min(30, curr_ratio * 0.15)

        dart_cache[corp_code] = round(score, 2)
        return dart_cache[corp_code]

    except Exception as e:
        print(f"[DART ERROR] {corp_code} {e}")
        dart_cache[corp_code] = 50
        return 50

# ─────────────────────────────────────────────
# 시장 상대강도
# ─────────────────────────────────────────────
def calc_market_return(market, history):
    """전체 시장 평균 1일 수익률"""
    rets = []
    for s in market[:200]:
        code  = s.get("ISU_CD")
        close = safe_int(s.get("TDD_CLSPRC", 0))
        hist  = history.get(code, [])
        if len(hist) >= 2 and close > 0:
            prev = hist[-1][1]
            if prev > 0:
                rets.append((close / prev - 1) * 100)
    return sum(rets) / len(rets) if rets else 0

def relative_strength_score(today_ret, market_ret):
    """종목 수익률 - 시장 평균 → 0~100점"""
    diff = today_ret - market_ret
    if diff >  5:   return 95
    if diff >  2:   return 80
    if diff >  0.5: return 65
    if diff > -0.5: return 50
    if diff > -2:   return 35
    if diff > -5:   return 20
    return 5

# ─────────────────────────────────────────────
# 신호 분류
# ─────────────────────────────────────────────
def classify_signal(final_score):
    if final_score >= 80: return "STRONG BUY"
    if final_score >= 65: return "BUY"
    if final_score >= 45: return "WATCH"
    if final_score >= 30: return "SELL"
    return "STRONG SELL"

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("[ENGINE v9.0 MULTI-FACTOR START]")

    dart_key = os.getenv("DART_API_KEY")
    print(f"[CHECK] DART KEY: {bool(dart_key)}")

    # corp_map 로드
    if not os.path.exists(CORP_MAP_PATH):
        raise RuntimeError("corp_map.json missing")
    with open(CORP_MAP_PATH, "r", encoding="utf-8") as f:
        corp_map = json.load(f)
    print(f"[CORP MAP] {len(corp_map)} entries")

    # 시장 데이터
    market, used_date = load_market()
    if not market:
        raise RuntimeError("KRX FAILED: empty market data")
    print(f"[MARKET SIZE] {len(market)}")

    # 히스토리 로드 (RSI / 모멘텀 / 거래량 계산용)
    history = load_history()
    print(f"[HISTORY] {len(history)} stocks tracked")

    # 시장 평균 수익률
    market_ret = calc_market_return(market, history)
    print(f"[MARKET RETURN] {market_ret:.2f}%")

    # 시총 상위 200
    market_map = {}
    for s in market:
        code  = s.get("ISU_CD")
        mcap  = safe_int(s.get("MKTCAP", 0))
        if code and mcap > 0:
            market_map[code] = (mcap, s)
    universe = [k for k, _ in sorted(market_map.items(),
                key=lambda x: x[1][0], reverse=True)][:200]

    results = []

    for i, code in enumerate(universe, 1):
        _, s = market_map[code]

        close = safe_int(s.get("TDD_CLSPRC", 0))
        vol   = safe_int(s.get("ACC_TRDVOL", 0))
        if close == 0:
            continue

        corp_info = corp_map.get(code)
        if not corp_info:
            continue
        corp_code = corp_info.get("corp_code", "")
        name      = corp_info.get("name", code)

        # 히스토리 데이터
        hist      = history.get(code, [])
        prices    = [h[1] for h in hist]
        hist_vols = [h[2] for h in hist]

        # ── 팩터 계산 ─────────────────────────────

        # 1. RSI
        rsi = calc_rsi(prices + [close])
        f_rsi = rsi_score(rsi)

        # 2. 거래량 서지
        f_vol = volume_surge_score(vol, hist_vols)

        # 3. DART 재무 (0~100 스케일)
        dart_raw = get_dart_financial(corp_code, dart_key)
        f_dart   = min(100, dart_raw)

        # 4. 시장 상대강도
        prev_close = prices[-1] if prices else close
        today_ret  = (close / prev_close - 1) * 100 if prev_close > 0 else 0
        f_relative = relative_strength_score(today_ret, market_ret)

        # 5. 모멘텀
        f_momentum = momentum_score(prices + [close])

        # ── 최종 점수 (0~100) ─────────────────────
        final = (
            f_rsi      * W_RSI      +
            f_vol      * W_VOLUME   +
            f_dart     * W_DART     +
            f_relative * W_RELATIVE +
            f_momentum * W_MOMENTUM
        )
        final = round(min(100, max(0, final)), 2)

        signal = classify_signal(final)

        results.append({
            "code":       code,
            "name":       name,
            "score":      final,
            "signal":     signal,
            "dart_score": dart_raw,
            "rsi":        round(rsi, 1) if rsi is not None else None,
            "factors": {
                "rsi":      round(f_rsi, 1),
                "volume":   round(f_vol, 1),
                "dart":     round(f_dart, 1),
                "relative": round(f_relative, 1),
                "momentum": round(f_momentum, 1),
            },
            "close":      close,
            "volume":     vol,
            "today_ret":  round(today_ret, 2),
        })

        if i % 20 == 0:
            print(f"[{i}/200] score={final:.1f} rsi={rsi:.1f if rsi else 'N/A'} dart={dart_raw:.1f}")

        time.sleep(0.03)

    if not results:
        raise RuntimeError("NO RESULTS GENERATED")

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time":        datetime.now().isoformat(),
        "data_date":   used_date,
        "mode":        "v9.0_multifactor",
        "market_ret":  round(market_ret, 2),
        "top10":       results[:10],
        "all":         results,
    }

    # 백업 & 저장
    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # 히스토리 추가 (code,date,close,volume,score,dart)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        for r in results:
            f.write(f'{r["code"]},{used_date},{r["close"]},{r["volume"]},{r["score"]},{r["dart_score"]}\n')

    top = results[0]
    print(f"[DONE] Top1: {top['name']} / score:{top['score']} / signal:{top['signal']} / RSI:{top['rsi']}")

if __name__ == "__main__":
    main()
