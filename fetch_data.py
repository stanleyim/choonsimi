"""
fetch_data.py — v8.5
- KRX 전종목 가격/거래량/등락률 수집
- KS11 종가 → market_flow.json 자동 누적 저장 (신규)
"""

import io
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

DATA_PATH        = "data.json"
MARKET_FLOW_PATH = "market_flow.json"
KST              = timezone(timedelta(hours=9))

FDR_URL     = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/listing/krx/{}.csv"
KS11_URL    = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/master/data/index/KS11/{}.csv"
MAX_RETRIES = 7


def get_trading_dates():
    dates = []
    cur = datetime.now(timezone.utc)
    while len(dates) < MAX_RETRIES:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates


def fetch_fdr():
    last_error = None
    for date in get_trading_dates():
        url = FDR_URL.format(date)
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                print(f"[FDR] {date} HTTP {r.status_code} skip")
                continue

            df = pd.read_csv(io.StringIO(r.text))
            if len(df) < 50:
                continue

            col_map = {
                "Code":        "code",
                "Symbol":      "code",
                "Name":        "name",
                "Close":       "close",
                "Adj Close":   "close",
                "Volume":      "volume",
                "ChagesRatio": "change_rate",
                "ChgRatio":    "change_rate",
                "ChangeRatio": "change_rate",
                "Chg":         "change_rate",
                "Change":      "change_rate",
                "Returns":     "change_rate",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            print(f"[FDR] {date} columns: {list(df.columns)}")

            required = ["code", "close", "volume"]
            if not all(c in df.columns for c in required):
                print(f"[FDR] {date} missing columns → skip")
                continue

            df["code"]   = df["code"].astype(str).str.zfill(6)
            df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            df = df.dropna(subset=["close", "volume"])
            df = df[(df["close"] > 0) & (df["volume"] > 0)]

            if len(df) < 50:
                continue

            if "change_rate" in df.columns:
                df["change_rate"] = pd.to_numeric(df["change_rate"], errors="coerce").fillna(0)
                print(f"[FDR] change_rate 컬럼 확보 ✅")
            else:
                df["change_rate"] = 0.0
                print(f"[FDR] change_rate 컬럼 없음 → 0 처리")

            df = df[["code", "name", "close", "volume", "change_rate"]].copy()
            print(f"[FDR] {date} OK → {len(df)} rows")
            return df, date

        except Exception as e:
            last_error = e
            print(f"[FDR] {date} error → skip: {e}")

    raise RuntimeError(f"FDR fetch failed: {last_error}")


def fetch_ks11_close(date: str) -> float:
    """
    KS11 당일 종가 수집
    FDR KRX cache에서 직접 가져옴 (Yahoo 429 문제 우회)
    """
    try:
        url = KS11_URL.format(date)
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None

        df = pd.read_csv(io.StringIO(r.text))
        if df.empty:
            return None

        # Close 컬럼 찾기
        for col in ["Close", "close", "종가"]:
            if col in df.columns:
                val = pd.to_numeric(df[col].iloc[-1], errors="coerce")
                if val and val > 0:
                    return round(float(val), 2)

        return None
    except Exception as e:
        print(f"[KS11] 수집 실패: {e}")
        return None


def update_market_flow(date: str, kospi: float):
    """
    market_flow.json에 오늘 KOSPI 종가 자동 누적
    기존 날짜 중복 방지
    최근 300일치만 유지
    """
    try:
        existing = []
        try:
            with open(MARKET_FLOW_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except:
            pass

        # 오늘 날짜 중복 제거
        existing = [r for r in existing if r.get("date") != date]

        # 오늘치 추가 (kospi만, foreign_net/inst_net은 형이 수동 입력)
        new_row = {"date": date, "kospi": kospi}
        existing.append(new_row)

        # 날짜순 정렬
        existing = sorted(existing, key=lambda x: x.get("date", ""))

        # 최근 300일치만 유지
        existing = existing[-300:]

        with open(MARKET_FLOW_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        print(f"[KS11] market_flow.json 업데이트 → kospi={kospi} ({len(existing)}일치)")

    except Exception as e:
        print(f"[KS11] market_flow.json 업데이트 실패: {e}")


def save(df, date):
    payload = {
        "date":  date,
        "count": len(df),
        "all":   df.to_dict("records"),
    }
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[SAVE] data.json updated")


if __name__ == "__main__":
    print("[FETCH START]")

    # 1. KRX 전종목 데이터
    df, date = fetch_fdr()
    save(df, date)

    # 2. KS11 종가 → market_flow.json 자동 누적
    kospi = fetch_ks11_close(date)
    if kospi:
        update_market_flow(date, kospi)
        print(f"[KS11] {date} 종가: {kospi}")
    else:
        print(f"[KS11] {date} 종가 수집 실패 → market_flow.json 유지")

    print("[FETCH DONE]")
