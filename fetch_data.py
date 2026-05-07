"""
fetch_data.py — v4.1
────────────────────────────────────────────────────────────
KIS API → 당일 종목 데이터 수집
토큰을 kis_token.json 에 저장 → fetch_kis_flow.py 재사용

수집 전략:
  1) FHPST01710000 거래량순위 (J=전체, 30종목)
  2) market_flow.json rows 종목 개별 시세 보완
  → 합산 후 거래대금 기준 정렬

환경변수: KIS_APP_KEY, KIS_APP_SECRET
────────────────────────────────────────────────────────────
"""

import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

KIS_BASE   = "https://openapi.koreainvestment.com:9443"
TIMEOUT    = 10
DELAY      = 0.2
OUTPUT_CSV = "history.csv"
TOKEN_FILE = "kis_token.json"
FLOW_FILE  = "market_flow.json"
MAX_STOCKS = 600
KST        = timezone(timedelta(hours=9))


# ── 토큰 발급 + 저장 ────────────────────────────────────────

def get_token() -> str:
    """토큰 발급 후 kis_token.json 저장 (fetch_kis_flow.py 재사용)"""
    res = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey":     os.environ["KIS_APP_KEY"],
            "appsecret":  os.environ["KIS_APP_SECRET"],
        },
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    token = res.json()["access_token"]

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "access_token": token,
            "issued_at":    datetime.now(KST).isoformat(),
        }, f)
    print(f"[AUTH] 토큰 발급 완료 → {TOKEN_FILE} 저장")
    return token


def make_headers(token: str) -> dict:
    return {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "custtype":      "P",
    }


# ── 거래량 순위 (전체 30종목) ────────────────────────────────

def fetch_volume_rank(token: str) -> list:
    """FHPST01710000 — 거래량 순위 (J=전체, 최대 30개)"""
    headers = {**make_headers(token), "tr_id": "FHPST01710000"}
    params  = {
        "FID_COND_MRKT_DIV_CODE":  "J",
        "FID_COND_SCR_DIV_CODE":   "20171",
        "FID_INPUT_ISCD":          "0000",
        "FID_DIV_CLS_CODE":        "0",
        "FID_BLNG_CLS_CODE":       "0",
        "FID_TRGT_CLS_CODE":       "111111111",
        "FID_TRGT_EXLS_CLS_CODE":  "0000000000",
        "FID_INPUT_PRICE_1":       "0",
        "FID_INPUT_PRICE_2":       "0",
        "FID_VOL_CNT":             "0",
        "FID_INPUT_DATE_1":        "",
    }
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=headers, params=params, timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("rt_cd") != "0":
            print(f"[WARN] 거래량순위 오류: {data.get('msg1','')}")
            return []
        rows = []
        for item in data.get("output", []):
            try:
                code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
                close       = int(str(item.get("stck_prpr",    "0")).replace(",","") or 0)
                volume      = int(str(item.get("acml_vol",     "0")).replace(",","") or 0)
                change_rate = float(str(item.get("prdy_ctrt",  "0")).replace(",","") or 0)
                value       = int(str(item.get("acml_tr_pbmn", "0")).replace(",","") or 0)
                name        = item.get("hts_kor_isnm", "")
                if code and close > 0:
                    rows.append({"code": code, "name": name, "close": close,
                                 "volume": volume, "change_rate": change_rate,
                                 "value": value})
            except Exception:
                continue
        print(f"[DATA] 거래량순위: {len(rows)}종목")
        return rows
    except Exception as e:
        print(f"[WARN] 거래량순위 실패: {e}")
        return []


# ── market_flow.json rows 종목 개별 시세 보완 ────────────────

def get_flow_codes() -> list:
    """market_flow.json rows 에서 종목 코드 추출"""
    try:
        with open(FLOW_FILE, "r", encoding="utf-8") as f:
            flow = json.load(f)
        codes = set()
        for seg in ("KOSPI_foreign","KOSPI_institution",
                    "KOSDAQ_foreign","KOSDAQ_institution"):
            for row in flow.get(seg, {}).get("rows", []):
                code = str(row.get("code","")).zfill(6)
                if code:
                    codes.add(code)
        return list(codes)
    except Exception:
        return []


def fetch_price(token: str, code: str) -> dict:
    """FHKST01010100 — 개별 종목 현재가"""
    headers = {**make_headers(token), "tr_id": "FHKST01010100"}
    params  = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD":         code,
    }
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers, params=params, timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("rt_cd") != "0":
            return {}
        o = data.get("output", {})
        close       = int(str(o.get("stck_prpr",   "0")).replace(",","") or 0)
        volume      = int(str(o.get("acml_vol",    "0")).replace(",","") or 0)
        change_rate = float(str(o.get("prdy_ctrt", "0")).replace(",","") or 0)
        value       = close * volume
        name        = o.get("hts_kor_isnm", "")
        if close > 0:
            return {"code": code, "name": name, "close": close,
                    "volume": volume, "change_rate": change_rate,
                    "value": value}
    except Exception:
        pass
    return {}


def fetch_flow_prices(token: str, known_codes: set) -> list:
    """market_flow rows 중 거래량순위에 없는 종목 보완"""
    codes = [c for c in get_flow_codes() if c not in known_codes]
    if not codes:
        return []
    print(f"[DATA] flow rows 보완 대상: {len(codes)}종목")
    rows = []
    for code in codes:
        result = fetch_price(token, code)
        if result:
            rows.append(result)
        time.sleep(DELAY)
    print(f"[DATA] flow rows 보완 완료: {len(rows)}종목")
    return rows


# ── 메인 ────────────────────────────────────────────────────

def main():
    now       = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    print(f"[DATA] {today_str} 수집 시작 (KIS API)")

    try:
        token = get_token()
    except Exception as e:
        print(f"[ERROR] 토큰 발급 실패: {e}")
        return

    # ① 거래량 순위 (30종목)
    rank_rows  = fetch_volume_rank(token)
    known_codes = {r["code"] for r in rank_rows}

    # ② market_flow rows 보완
    time.sleep(0.5)
    flow_rows = fetch_flow_prices(token, known_codes)

    all_rows = rank_rows + flow_rows
    if not all_rows:
        print("[ERROR] 전체 데이터 수집 실패 → 종료")
        return

    # ③ DataFrame 변환 + 중복 제거 + 정렬
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["code"], keep="first")
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    df_top = df.nlargest(MAX_STOCKS, "value").copy()
    df_top["date"] = today_str

    result = df_top[["date","code","name","close","volume","change_rate"]
                    ].reset_index(drop=True)

    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[DONE] {len(result)}종목 → {OUTPUT_CSV} 저장 완료")
    print(f"       change_rate 샘플: {result['change_rate'].head(3).tolist()}")


if __name__ == "__main__":
    main()
