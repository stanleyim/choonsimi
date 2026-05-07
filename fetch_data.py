"""
fetch_data.py — v3.1
────────────────────────────────────────────────────────────
finance-datareader → KRX 전체 종목 당일 데이터 수집
(pykrx KRX 로그인 문제 해결 → FDR로 교체)

StockListing('KRX') 한 번 호출로
  KOSPI + KOSDAQ 전체 종목
  종가 / 거래량 / 등락률 / 거래대금 취득

거래대금 상위 600종목 필터링
출력: history.csv (date, code, name, close, volume, change_rate)
────────────────────────────────────────────────────────────
"""

import pandas as pd
from datetime import datetime, timezone, timedelta

try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None

KST        = timezone(timedelta(hours=9))
OUTPUT_CSV = "history.csv"
MAX_STOCKS = 600


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    FDR 버전별 컬럼명 차이를 흡수
    Code/Name/Close/Volume/ChagesRatio(오타포함)/Amount 등
    """
    rename = {}
    for col in df.columns:
        cl = col.lower().replace(" ", "")
        if cl == "code":
            rename[col] = "code"
        elif cl == "name":
            rename[col] = "name"
        elif cl == "close":
            rename[col] = "close"
        elif cl == "volume":
            rename[col] = "volume"
        elif "amount" in cl:
            rename[col] = "value"
        elif "ratio" in cl or cl in ("chgrt", "chagesratio", "changesratio"):
            rename[col] = "change_rate"

    return df.rename(columns=rename)


def main():
    if fdr is None:
        print("[ERROR] FinanceDataReader 미설치 → pip install finance-datareader")
        return

    now       = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")

    print(f"[DATA] {today_str} 수집 시작")

    # ── KRX 전체 종목 당일 데이터 ────────────────────────────
    try:
        df_raw = fdr.StockListing("KRX")
        print(f"[DATA] KRX 전체 {len(df_raw)}종목 수신")
    except Exception as e:
        print(f"[ERROR] StockListing('KRX') 실패: {e}")
        return

    if df_raw is None or df_raw.empty:
        print("[ERROR] 데이터 없음 → 종료")
        return

    # ── 컬럼 정규화 ──────────────────────────────────────────
    df = normalize_columns(df_raw)

    required = {"code", "name", "close", "volume"}
    missing  = required - set(df.columns)
    if missing:
        print(f"[ERROR] 필수 컬럼 없음: {missing}")
        print(f"  실제 컬럼: {list(df_raw.columns)}")
        return

    # ── 타입 변환 ────────────────────────────────────────────
    df["code"]   = df["code"].astype(str).str.zfill(6)
    df["close"]  = pd.to_numeric(df["close"],  errors="coerce").fillna(0)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    if "change_rate" in df.columns:
        df["change_rate"] = pd.to_numeric(
            df["change_rate"], errors="coerce"
        ).fillna(0.0)
    else:
        print("[WARN] 등락률 컬럼 없음 → 0.0 으로 채움")
        df["change_rate"] = 0.0

    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    else:
        df["value"] = df["volume"] * df["close"]

    # ── 거래대금 상위 MAX_STOCKS ─────────────────────────────
    df_top = df.nlargest(MAX_STOCKS, "value").copy()
    print(f"[DATA] 거래대금 상위 {len(df_top)}종목 선택")

    # ── 날짜 추가 + 최종 컬럼 정리 ──────────────────────────
    df_top["date"] = today_str
    result = df_top[["date", "code", "name", "close", "volume", "change_rate"]
                    ].reset_index(drop=True)

    # ── CSV 저장 ─────────────────────────────────────────────
    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[DONE] {len(result)}종목 → {OUTPUT_CSV} 저장 완료")
    print(f"       change_rate 샘플: {result['change_rate'].head(3).tolist()}")


if __name__ == "__main__":
    main()
