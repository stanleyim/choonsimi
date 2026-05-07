"""
fetch_data.py — v4.0
────────────────────────────────────────────────────────────
KIS API 거래량 순위 → 당일 종목 데이터 수집
(FDR/pykrx KRX 직접 조회 실패 → KIS API로 완전 교체)

FHPST01710000 (거래량순위) KOSPI + KOSDAQ 각각 조회
→ 거래대금 상위 600종목 필터링
출력: history.csv (date, code, name, close, volume, change_rate)

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
DELAY      = 0.3
OUTPUT_CSV = "history.csv"
MAX_STOCKS = 600
KST        = timezone(timedelta(hours=9))


# ── 인증 ────────────────────────────────────────────────────

def get_token() -> str:
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
    return res.json()["access_token"]


# ── 거래량 순위 조회 ─────────────────────────────────────────

def fetch_volume_rank(token: str, market_code: str) -> list:
    """
    FHPST01710000 — 거래량 순위
    market_code: "J" = KOSPI, "Q" = KOSDAQ
    반환: [{code, name, close, volume, change_rate, value}, ...]
    """
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "tr_id":         "FHPST01710000",
        "custtype":      "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE":  market_code,
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
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()

        if data.get("rt_cd") != "0":
            print(f"[WARN] {market_code} 거래량순위 API 오류: {data.get('msg1','')}")
            return []

        rows = []
        for item in data.get("output", []):
            try:
                code        = str(item.get("mksc_shrn_iscd", "")).zfill(6)
                name        = item.get("hts_kor_isnm", "")
                close       = int(str(item.get("stck_prpr",  "0")).replace(",", "") or 0)
                volume      = int(str(item.get("acml_vol",   "0")).replace(",", "") or 0)
                change_rate = float(str(item.get("prdy_ctrt","0")).replace(",", "") or 0)
                value       = int(str(item.get("acml_tr_pbmn", "0")).replace(",", "") or 0)

                if code and close > 0:
                    rows.append({
                        "code":        code,
                        "name":        name,
                        "close":       close,
                        "volume":      volume,
                        "change_rate": change_rate,
                        "value":       value,
                    })
            except Exception:
                continue

        return rows

    except Exception as e:
        print(f"[WARN] {market_code} 거래량순위 수집 실패: {e}")
        return []


# ── 메인 ────────────────────────────────────────────────────

def main():
    now       = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")

    print(f"[DATA] {today_str} 수집 시작 (KIS API)")

    # ── 토큰 발급 ─────────────────────────────────────────
    try:
        token = get_token()
        print("[DATA] KIS 토큰 발급 완료")
    except Exception as e:
        print(f"[ERROR] 토큰 발급 실패: {e}")
        return

    # ── KOSPI + KOSDAQ 거래량 순위 조회 ───────────────────
    all_rows = []
    for market_code, market_name in [("J", "KOSPI"), ("Q", "KOSDAQ")]:
        rows = fetch_volume_rank(token, market_code)
        print(f"[DATA] {market_name}: {len(rows)}종목")
        all_rows.extend(rows)
        time.sleep(DELAY)

    if not all_rows:
        print("[ERROR] 전체 데이터 수집 실패 → 종료")
        return

    # ── DataFrame 변환 + 중복 제거 ────────────────────────
    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["code"], keep="first")
    print(f"[DATA] 합산 {len(df)}종목 (중복 제거 후)")

    # ── 거래대금 상위 MAX_STOCKS ──────────────────────────
    df_top = df.nlargest(MAX_STOCKS, "value").copy()
    print(f"[DATA] 거래대금 상위 {len(df_top)}종목 선택")

    # ── 날짜 추가 + 최종 컬럼 ────────────────────────────
    df_top["date"] = today_str
    result = df_top[["date", "code", "name", "close", "volume", "change_rate"]
                    ].reset_index(drop=True)

    # ── CSV 저장 ──────────────────────────────────────────
    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[DONE] {len(result)}종목 → {OUTPUT_CSV} 저장 완료")
    print(f"       change_rate 샘플: {result['change_rate'].head(3).tolist()}")


if __name__ == "__main__":
    main()
