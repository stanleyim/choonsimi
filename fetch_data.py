"""
fetch_data.py — v3.2
────────────────────────────────────────────────────────────
FinanceDataReader → KRX 전체 종목 30일 데이터 수집
거래대금 상위 600종목 필터링 + 섹터매핑 저장
출력: history.csv (date, code, close, volume), sector_map.json
────────────────────────────────────────────────────────────
"""

import pandas as pd, json
from datetime import datetime, timezone, timedelta

try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None

KST        = timezone(timedelta(hours=9))
OUTPUT_CSV = "history.csv"
SECTOR_JSON = "sector_map.json"
MAX_STOCKS = 600
DAYS_LOOKBACK = 30

def main():
    if fdr is None:
        print("[ERROR] FinanceDataReader 미설치 → pip install finance-datareader")
        return

    now       = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    start_str = (now - timedelta(days=DAYS_LOOKBACK)).strftime("%Y-%m-%d")

    print(f"[DATA] {today_str} KRX 30일 데이터 수집 시작")

    # ── KRX 전체 종목 리스트 + 섹터 ───────────────────────────
    try:
        tickers = fdr.StockListing("KRX")
        print(f"[DATA] KRX 전체 {len(tickers)}종목 수신")
    except Exception as e:
        print(f"[ERROR] StockListing('KRX') 실패: {e}")
        return

    sector_map = dict(zip(tickers["Code"], tickers.get("Sector", "기타")))
    history_list = []

    # ── 종목별 30일 데이터 수집 ───────────────────────────────
    for code in tickers["Code"]:
        try:
            df = fdr.DataReader(code, start_str, today_str)
            if len(df) >= 20:  # 20일 모멘텀 계산 최소 조건
                df = df.reset_index()
                df["code"] = code
                history_list.append(df[["Date","Open","High","Low","Close","Volume","code"]]
                                   .rename(columns={"Date":"date","Close":"close","Volume":"volume"}))
        except Exception:
            continue

    if not history_list:
        print("[ERROR] 30일 데이터 수집 실패 → 종료")
        return

    history_df = pd.concat(history_list, ignore_index=True)

    # ── 당일 거래대금 상위 600종목만 필터링 ───────────────────
    today_df = history_df[history_df["date"] == today_str].copy()
    if today_df.empty:
        print("[WARN] 당일 데이터 없음 → 가장 최근 날짜 사용")
        last_date = history_df["date"].max()
        today_df = history_df[history_df["date"] == last_date].copy()

    today_df["value"] = today_df["volume"] * today_df["close"]
    top600_codes = today_df.nlargest(MAX_STOCKS, "value")["code"].tolist()

    history_df = history_df[history_df["code"].isin(top600_codes)]
    print(f"[DATA] 거래대금 상위 {len(top600_codes)}종목, 총 {len(history_df)}행 저장")

    # ── CSV 저장 ─────────────────────────────────────────────
    history_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    
    # ── 섹터맵 저장 ──────────────────────────────────────────
    sector_map_filtered = {k: v for k, v in sector_map.items() if k in top600_codes}
    with open(SECTOR_JSON, "w", encoding="utf-8") as f:
        json.dump(sector_map_filtered, f, indent=2, ensure_ascii=False)

    print(f"[DONE] {OUTPUT_CSV} {len(history_df)}행 저장 완료")
    print(f"[DONE] {SECTOR_JSON} {len(sector_map_filtered)}종목 저장 완료")
    print(f"       날짜 범위: {history_df['date'].min()} ~ {history_df['date'].max()}")

if __name__ == "__main__":
    main()
