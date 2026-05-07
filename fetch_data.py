"""
fetch_data.py — v3.0
────────────────────────────────────────────────────────────
pykrx → 당일 KOSPI+KOSDAQ OHLCV 수집
거래대금 상위 600 종목만 필터링
출력: history.csv

컬럼: date, code, name, close, volume, change_rate
────────────────────────────────────────────────────────────
"""

import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from pykrx import stock as krx

KST        = timezone(timedelta(hours=9))
OUTPUT_CSV = "history.csv"
MAX_STOCKS = 600   # 거래대금 상위 N종목

# pykrx 한글 컬럼 → 영문 매핑
COL_MAP = {
    "시가":     "open",
    "고가":     "high",
    "저가":     "low",
    "종가":     "close",
    "거래량":   "volume",
    "거래대금": "value",
    "등락률":   "change_rate",
}


def fetch_ohlcv(today: str) -> pd.DataFrame:
    """KOSPI + KOSDAQ OHLCV 통합 수집"""
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = krx.get_market_ohlcv_by_ticker(today, market=market)
            if df is None or df.empty:
                print(f"[WARN] {market} 데이터 없음")
                continue
            df = df.rename(columns=COL_MAP)
            df.index = df.index.astype(str).str.zfill(6)
            df.index.name = "code"
            df = df.reset_index()
            df["market"] = market
            frames.append(df)
            print(f"[DATA] {market}: {len(df)}종목")
        except Exception as e:
            print(f"[WARN] {market} OHLCV 수집 실패: {e}")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def fetch_names(codes: list) -> dict:
    """종목명 일괄 조회"""
    names = {}
    for code in codes:
        try:
            names[code] = krx.get_market_ticker_name(str(code))
        except Exception:
            names[code] = ""
    return names


def main():
    now       = datetime.now(KST)
    today     = now.strftime("%Y%m%d")
    today_str = now.strftime("%Y-%m-%d")

    print(f"[DATA] {today_str} 수집 시작")

    # ── OHLCV 수집 ───────────────────────────────────────────
    df_all = fetch_ohlcv(today)
    if df_all.empty:
        print("[ERROR] OHLCV 수집 전체 실패 → 종료")
        return

    df_all["code"] = df_all["code"].astype(str).str.zfill(6)

    # ── 거래대금 상위 MAX_STOCKS 필터 ────────────────────────
    if "value" in df_all.columns:
        df_top = df_all.nlargest(MAX_STOCKS, "value").copy()
        print(f"[DATA] 거래대금 상위 {len(df_top)}종목 선택")
    else:
        print("[WARN] 거래대금 컬럼 없음 → 전체 사용")
        df_top = df_all.head(MAX_STOCKS).copy()

    # ── 종목명 추가 ───────────────────────────────────────────
    codes     = df_top["code"].unique().tolist()
    name_map  = fetch_names(codes)
    df_top["name"]        = df_top["code"].map(name_map).fillna("")
    df_top["date"]        = today_str
    df_top["change_rate"] = pd.to_numeric(
        df_top.get("change_rate", 0), errors="coerce"
    ).fillna(0.0)

    # ── 필요 컬럼 선택 ────────────────────────────────────────
    need_cols = ["date", "code", "name", "close", "volume", "change_rate"]
    avail     = [c for c in need_cols if c in df_top.columns]
    result    = df_top[avail].reset_index(drop=True)

    # ── CSV 저장 ─────────────────────────────────────────────
    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[DONE] {len(result)}종목 → {OUTPUT_CSV} 저장 완료")
    print(f"       change_rate 샘플: {result['change_rate'].head(3).tolist()}")


if __name__ == "__main__":
    main()
