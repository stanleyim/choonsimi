"""
fetch_data.py — v5.0.0 DAILY-PRICE REBUILD
─────────────────────────────────────────────────────────
목적  : 장마감 확정 OHLCV 데이터 수집 → history.csv 저장
방식  : KIS 일별시세 API (FHKST03010100)
        → 실시간 현재가 API 대비 volume 리셋 문제 완전 해결
        → 언제 실행해도 항상 가장 최근 확정 데이터
─────────────────────────────────────────────────────────
v5.0.0 vs v4.8.2 변경점
  ✔ inquire-price → inquire-daily-price 교체 (volume 리셋 해결)
  ✔ open / high / low 컬럼 추가 (캔들 패턴 분석 대비)
  ✔ trade_value (거래대금) 추가 (10/20일 분석 대비)
  ✔ market_codes 정상화: ["0000","0004"] → ["0001","0002"]
  ✔ .seconds → .total_seconds() 버그 수정
  ✔ 유니버스: volume_rank(장중 한정) → flow 종목 + 일별시세 조합
  ✔ history.csv 누적 저장 (날짜별 append, 오늘 날짜 덮어쓰기)
─────────────────────────────────────────────────────────
history.csv 컬럼 (10/20일 분석 완전 대비)
  date, code, name, open, high, low, close, volume, trade_value, change_rate
─────────────────────────────────────────────────────────
"""

import os, json, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

KIS_BASE   = "https://openapi.koreainvestment.com:9443"
TIMEOUT    = 10
DELAY      = 0.25
MAX_RETRY  = 3

OUTPUT_CSV = "history.csv"
TOKEN_FILE = "kis_token.json"
FLOW_FILE  = "market_flow.json"

KST        = timezone(timedelta(hours=9))
MAX_STOCKS = 200

# history.csv 보존 기간 (일): 60일치 유지
KEEP_DAYS  = 60

BLOCK_KEYWORDS = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
    "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
    "레버리지","인버스","선물","REIT","리츠","INDEX","지수"
]

# ── 최종 컬럼 순서 ────────────────────────────────────
COLUMNS = ["date","code","name","open","high","low","close","volume","trade_value","change_rate"]


# ══════════════════════════════════════════════════════
# UTILS
# ══════════════════════════════════════════════════════
def safe_float(v, d=0.0):
    try:    return float(str(v).replace(",",""))
    except: return d

def safe_int(v, d=0):
    try:    return int(str(v).replace(",",""))
    except: return d

def is_common_stock(code, name):
    code = str(code).strip()
    name = str(name or "").strip()
    if not code.isdigit() or len(code) != 6: return False
    if code[-1] in ("5","7","9"):            return False
    if name.lower() in ("","nan","none"):     return False
    if any(k in name.upper() for k in BLOCK_KEYWORDS): return False
    return True


# ══════════════════════════════════════════════════════
# TOKEN
# ══════════════════════════════════════════════════════
def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        issued = datetime.fromisoformat(
            data.get("issued_at","").replace("Z","") or "2000-01-01T00:00:00"
        )
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=KST)
        # ✅ .total_seconds() 버그 수정
        if (datetime.now(KST) - issued).total_seconds() < 21600:
            return data.get("access_token")
    except: pass

    for _ in range(MAX_RETRY):
        try:
            r = requests.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     os.environ.get("KIS_APP_KEY",""),
                    "appsecret":  os.environ.get("KIS_APP_SECRET","")
                },
                timeout=TIMEOUT
            )
            r.raise_for_status()
            token = r.json().get("access_token")
            with open(TOKEN_FILE,"w",encoding="utf-8-sig") as f:
                json.dump({
                    "access_token": token,
                    "issued_at":    datetime.now(KST).isoformat()
                }, f)
            return token
        except: time.sleep(1)
    return None

def kis_headers(token, tr_id):
    return {
        "authorization": f"Bearer {token}",
        "appkey":        os.environ.get("KIS_APP_KEY",""),
        "appsecret":     os.environ.get("KIS_APP_SECRET",""),
        "tr_id":         tr_id,
        "content-type":  "application/json",
        "custtype":      "P"
    }


# ══════════════════════════════════════════════════════
# 유니버스 수집 — volume_rank (장중 한정, 종목 목록만 사용)
# ══════════════════════════════════════════════════════
def fetch_universe_codes(token):
    """
    volume_rank에서 종목 코드 목록만 수집.
    장마감 후 volume=0 이어도 코드 목록은 유효.
    실제 가격/거래량은 일별시세 API로 별도 조회.
    """
    if not token:
        return []

    # ✅ KOSPI(0001) + KOSDAQ(0002)만 사용
    market_codes = ["0001", "0002"]
    codes = []
    seen  = set()

    for market_code in market_codes:
        params = {
            "FID_COND_MRKT_DIV_CODE":  "J",
            "FID_COND_SCR_DIV_CODE":   "20171",
            "FID_INPUT_ISCD":          market_code,
            "FID_DIV_CLS_CODE":        "0",
            "FID_BLNG_CLS_CODE":       "0",
            "FID_TRGT_CLS_CODE":       "111111111",
            "FID_TRGT_EXLS_CLS_CODE":  "0000000000",
            "FID_INPUT_PRICE_1":       "0",
            "FID_INPUT_PRICE_2":       "0",
            "FID_VOL_CNT":             "0",
            "FID_INPUT_DATE_1":        ""
        }

        for _ in range(MAX_RETRY):
            try:
                r = requests.get(
                    f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/volume-rank",
                    headers=kis_headers(token, "FHPST01710000"),
                    params=params,
                    timeout=TIMEOUT
                )
                if r.status_code == 401: break
                r.raise_for_status()
                data = r.json()
                if data.get("rt_cd") != "0": break

                rows = data.get("output") or data.get("output1") or []
                if not isinstance(rows, list): break

                for item in rows:
                    code = str(item.get("mksc_shrn_iscd","")).zfill(6)
                    name = item.get("hts_kor_isnm","")
                    if is_common_stock(code, name) and code not in seen:
                        seen.add(code)
                        codes.append(code)
                break
            except: time.sleep(DELAY)

        time.sleep(0.1)

    print(f"[UNIVERSE] volume_rank 종목수: {len(codes)}")
    return codes


# ══════════════════════════════════════════════════════
# flow.json 종목 코드 수집
# ══════════════════════════════════════════════════════
def get_flow_codes():
    try:
        with open(FLOW_FILE, encoding="utf-8-sig") as f:
            flow = json.load(f)
        codes = set()
        for seg in ["KOSPI_foreign","KOSPI_institution","KOSDAQ_foreign","KOSDAQ_institution"]:
            for r in flow.get(seg, {}).get("rows", []):
                c = str(r.get("code","")).zfill(6)
                if c.isdigit() and len(c) == 6:
                    codes.add(c)
        print(f"[UNIVERSE] flow 종목수: {len(codes)}")
        return list(codes)
    except:
        return []


# ══════════════════════════════════════════════════════
# 일별시세 API — 핵심
# FHKST03010100: 항상 확정 데이터 반환 (volume 리셋 없음)
# output[0] = 가장 최근 거래일 데이터
# ══════════════════════════════════════════════════════
def fetch_daily_price(token, code):
    """
    일별시세 조회 → 가장 최근 확정 데이터 1행 반환
    장중/장후 상관없이 항상 동일한 확정값
    """
    if not token: return {}

    for _ in range(MAX_RETRY):
        try:
            r = requests.get(
                f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                headers=kis_headers(token, "FHKST03010100"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         code,
                    "FID_PERIOD_DIV_CODE":    "D",   # 일별
                    "FID_ORG_ADJ_PRC":        "0",   # 수정주가
                },
                timeout=TIMEOUT
            )
            if r.status_code == 401: return {}
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0": return {}

            rows = d.get("output") or d.get("output1") or d.get("output2") or []
            if isinstance(rows, dict): rows = [rows]
            if not rows: return {}

            # output[0] = 가장 최근 거래일
            o = rows[0]

            # 종목명 확인 (별도 output 구조 대비)
            name = (
                d.get("output1", {}).get("hts_kor_isnm") if isinstance(d.get("output1"), dict)
                else o.get("hts_kor_isnm", "")
            ) or ""

            close  = safe_int(o.get("stck_clpr"))
            volume = safe_int(o.get("acml_vol"))

            if close == 0 or volume == 0:
                return {}

            trade_value = safe_int(o.get("acml_tr_pbmn"))
            if trade_value == 0:
                trade_value = close * volume

            return {
                "code"        : code,
                "name"        : name,
                "open"        : safe_int(o.get("stck_oprc")),
                "high"        : safe_int(o.get("stck_hgpr")),
                "low"         : safe_int(o.get("stck_lwpr")),
                "close"       : close,
                "volume"      : volume,
                "trade_value" : trade_value,
                "change_rate" : safe_float(o.get("prdy_ctrt")),
            }

        except: time.sleep(DELAY)
    return {}


# ══════════════════════════════════════════════════════
# 종목명 보완 — 일별시세에서 이름 못 가져왔을 때
# ══════════════════════════════════════════════════════
def fetch_name(token, code):
    """inquire-price로 종목명만 보완"""
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=kis_headers(token, "FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            timeout=TIMEOUT
        )
        if r.status_code != 200: return ""
        o = r.json().get("output") or {}
        if isinstance(o, list): o = o[0] if o else {}
        return o.get("hts_kor_isnm","")
    except: return ""


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════
def main():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"[START] fetch_data v5.0.0  {today}")

    token = get_token()
    if not token:
        print("[ERROR] KIS 토큰 발급 실패")
        return

    # ── 유니버스 코드 수집 ───────────────────────────
    rank_codes = fetch_universe_codes(token)
    flow_codes = get_flow_codes()

    # 합산 후 중복 제거 (rank 우선, flow 보완)
    all_codes = list(dict.fromkeys(rank_codes + [
        c for c in flow_codes if c not in set(rank_codes)
    ]))
    all_codes = all_codes[:MAX_STOCKS]
    print(f"[UNIVERSE] 최종 대상: {len(all_codes)}종목")

    # ── 일별시세 개별 조회 ───────────────────────────
    rows = []
    fail = 0

    for i, code in enumerate(all_codes, 1):
        data = fetch_daily_price(token, code)

        if not data:
            fail += 1
            time.sleep(DELAY)
            continue

        # 종목명 없으면 보완
        if not data.get("name"):
            data["name"] = fetch_name(token, code)

        # ETF/ETN 등 재확인
        if not is_common_stock(data["code"], data["name"]):
            continue

        rows.append(data)
        time.sleep(DELAY)

        if i % 50 == 0:
            print(f"  ⏳ {i}/{len(all_codes)} 처리중... 성공={len(rows)} 실패={fail}")

    print(f"[FETCH] 성공={len(rows)} 실패={fail}")

    if not rows:
        print("[ERROR] 수집된 데이터 없음")
        return

    # ── DataFrame 정리 ───────────────────────────────
    df_new = pd.DataFrame(rows)
    df_new["date"] = today
    df_new["code"] = df_new["code"].astype(str).str.zfill(6)

    # 거래대금 기준 상위 선별 (MAX_STOCKS 초과 시)
    if len(df_new) > MAX_STOCKS:
        df_new["_score"] = np.log1p(df_new["trade_value"])
        df_new = df_new.nlargest(MAX_STOCKS, "_score").drop(columns=["_score"])

    df_new = df_new[COLUMNS]

    # ── 누적 저장 (오늘 날짜 덮어쓰기 + 60일치 보존) ─
    try:
        df_old = pd.read_csv(OUTPUT_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df_old["code"] = df_old["code"].astype(str).str.zfill(6)

        # 오늘 날짜 제거 후 새 데이터 추가
        df_old = df_old[df_old["date"] != today]

        # 60일 초과분 제거
        if "date" in df_old.columns:
            all_dates = sorted(df_old["date"].unique(), reverse=True)
            keep_dates = all_dates[:KEEP_DAYS - 1]
            df_old = df_old[df_old["date"].isin(keep_dates)]

        df_final = pd.concat([df_old, df_new], ignore_index=True)

    except FileNotFoundError:
        df_final = df_new

    df_final.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    dates_count = df_final["date"].nunique()
    print(f"[DONE] {len(df_new)}종목 저장 | 누적 {dates_count}일치 보존")
    print(f"[COLUMNS] {COLUMNS}")


if __name__ == "__main__":
    main()
