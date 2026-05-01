"""
fetch_data.py  —  Universe Builder Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
역할 : KRX 전종목(KOSPI + KOSDAQ) 당일 데이터를 수집해
       data.json {"all": [...]} 형태로 저장한다.

파이프라인 위치:
  fetch_data.py → engine/engine.py → result.json

필요 패키지: pykrx, pandas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import json
import os
import shutil
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

# ── CONFIG ───────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.abspath(__file__))
DATA_FILE   = os.path.join(ROOT, "data.json")
BACKUP_FILE = os.path.join(ROOT, "data.json.bak")

MIN_STOCKS  = 50     # 최소 종목 수 미달 시 에러
MAX_RETRIES = 7      # 거래일 소급 최대 횟수 (공휴일 대응)
SLEEP_SEC   = 0.4    # pykrx API 호출 간 딜레이
# ─────────────────────────────────────────────────────────


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1 : 유효 거래일 탐색
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_trading_date() -> str:
    """
    오늘부터 MAX_RETRIES일 소급하며 실제 거래 데이터가 있는 날짜 반환.
    주말 / 공휴일 자동 스킵.
    """
    for i in range(MAX_RETRIES):
        d = (datetime.today() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
            if df is not None and len(df) >= MIN_STOCKS:
                print(f"  [DATE] 거래일 확인: {d}  KOSPI {len(df)}종목")
                return d
        except Exception as e:
            print(f"  [SKIP] {d} 조회 실패: {e}")
        time.sleep(SLEEP_SEC)

    raise RuntimeError(f"최근 {MAX_RETRIES}일 내 유효 거래일 없음")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2 : 시장별 OHLCV 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_ohlcv(date: str, market: str) -> pd.DataFrame:
    """KOSPI 또는 KOSDAQ 전종목 OHLCV 수집."""
    print(f"  [FETCH] {market} {date} ...")
    try:
        df = stock.get_market_ohlcv_by_ticker(date, market=market)
    except Exception as e:
        print(f"  [WARN] {market} OHLCV 실패: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()

    # 컬럼명 한→영 매핑
    col_map = {
        "티커":    "code",
        "시가":    "open",
        "고가":    "high",
        "저가":    "low",
        "종가":    "close",
        "거래량":  "volume",
        "거래대금": "turnover",
        "등락률":  "change_pct",
    }
    df = df.rename(columns=col_map)

    # 필수 컬럼 존재 확인
    for col in ["code", "close", "volume"]:
        if col not in df.columns:
            print(f"  [WARN] {market} 필수 컬럼 '{col}' 없음 → skip")
            return pd.DataFrame()

    df["market"] = market
    time.sleep(SLEEP_SEC)
    return df


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3 : 종목명 수집 (선택)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_names(codes: list) -> dict:
    """종목 코드 → 종목명 딕셔너리."""
    name_map = {}
    for c in codes:
        try:
            name_map[c] = stock.get_market_ticker_name(c)
        except Exception:
            name_map[c] = ""
        time.sleep(0.05)
    return name_map


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 4 : universe 통합 + 정제
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_universe(date: str) -> list:
    """KOSPI + KOSDAQ 합산 → 정제 → list[dict] 반환."""

    frames = []
    for market in ["KOSPI", "KOSDAQ"]:
        df = fetch_ohlcv(date, market)
        if not df.empty:
            frames.append(df)

    if not frames:
        raise RuntimeError("KOSPI / KOSDAQ 데이터 수집 전부 실패")

    df = pd.concat(frames, ignore_index=True)

    # ── 타입 정제 ───────────────────────────────────
    df["code"]   = df["code"].astype(str).str.zfill(6)
    df["close"]  = pd.to_numeric(df["close"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
    df["open"]   = pd.to_numeric(df["open"]   if "open"   in df.columns else 0, errors="coerce").fillna(0)
    df["high"]   = pd.to_numeric(df["high"]   if "high"   in df.columns else 0, errors="coerce").fillna(0)
    df["low"]    = pd.to_numeric(df["low"]    if "low"    in df.columns else 0, errors="coerce").fillna(0)
    df["change_pct"] = pd.to_numeric(
        df["change_pct"] if "change_pct" in df.columns else 0, errors="coerce"
    ).fillna(0)

    # ── 유효 종목 필터 ──────────────────────────────
    df = df[df["close"]  > 0]
    df = df[df["volume"] > 0]
    df = df.drop_duplicates(subset=["code"])
    df = df.reset_index(drop=True)

    # ── engine이 기대하는 placeholder 컬럼 ─────────
    # flow.py = 의도된 고정 placeholder → 건드리지 않음
    df["foreign_net"] = 0
    df["inst_net"]    = 0
    df["dart_score"]  = 0
    df["date"]        = date

    # ── 종목명 (실패해도 빈 문자열로 계속 진행) ─────
    print(f"  [NAME] 종목명 수집 중 ({len(df)}종목) ...")
    try:
        name_map   = fetch_names(df["code"].tolist())
        df["name"] = df["code"].map(name_map).fillna("")
    except Exception as e:
        print(f"  [WARN] 종목명 수집 실패 (무시): {e}")
        df["name"] = ""

    print(f"  [UNIVERSE] 최종: {len(df)}종목  ({date})")

    # ── 최종 컬럼 순서 ───────────────────────────────
    cols = [
        "code", "name", "market", "date",
        "open", "high", "low", "close",
        "volume", "change_pct",
        "foreign_net", "inst_net", "dart_score",
    ]
    df = df[[c for c in cols if c in df.columns]]

    return df.to_dict("records")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 5 : data.json 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def save_data(records: list, date: str) -> None:
    """기존 data.json 백업 후 신규 저장."""

    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)
        print(f"  [BACKUP] {BACKUP_FILE}")

    payload = {
        "date":  date,
        "count": len(records),
        "all":   records,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"  [SAVE] {DATA_FILE}  →  {len(records)}종목")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENTRY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("[UNIVERSE BUILD START]")

    date    = get_trading_date()
    records = build_universe(date)

    if len(records) < MIN_STOCKS:
        raise RuntimeError(
            f"종목 수 부족: {len(records)}개 (최소 {MIN_STOCKS}개 필요)"
        )

    save_data(records, date)
    print("[UNIVERSE BUILD DONE]")
