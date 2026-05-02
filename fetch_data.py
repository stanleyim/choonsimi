"""
fetch_data.py  —  Universe Builder  v3  (3단계 Fallback)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1순위: FDR GitHub 캐시 CSV   (인증 불필요, 99% 가용)
2순위: FinanceDataReader 직접  (pip 설치, 인증 불필요)
3순위: 0값 placeholder        (파이프라인 생존 보장)

pykrx = KRX 로그인 필요 → 완전 제외
파이프라인: fetch_data.py → engine/engine.py → result.json
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import io
import json
import os
import shutil
from datetime import datetime, timedelta

import pandas as pd
import requests

ROOT        = os.path.dirname(os.path.abspath(__file__))
DATA_FILE   = os.path.join(ROOT, "data.json")
BACKUP_FILE = os.path.join(ROOT, "data.json.bak")

MIN_STOCKS  = 50
MAX_DAYS    = 10
TIMEOUT     = 20

FDR_CACHE_URL = (
    "https://raw.githubusercontent.com/"
    "FinanceData/fdr_krx_data_cache/"
    "refs/heads/master/data/listing/krx/{date}.csv"
)

COL_CANDIDATES = {
    "code":   ["Code", "Symbol", "code", "symbol", "티커"],
    "name":   ["Name", "ISU_ABBRV", "name", "종목명"],
    "market": ["Market", "MarketId", "market", "시장구분"],
    "close":  ["Close", "TDD_CLSPRC", "close", "종가"],
    "volume": ["Volume", "ACC_TRDVOL", "volume", "거래량"],
    "open":   ["Open",   "TDD_OPNPRC", "open",  "시가"],
    "high":   ["High",   "TDD_HGPRC",  "high",  "고가"],
    "low":    ["Low",    "TDD_LWPRC",  "low",   "저가"],
}

FALLBACK_CODES = [
    "005930", "000660", "035420", "005380", "051910",
    "035720", "006400", "028260", "003670", "068270",
    "105560", "055550", "032830", "086790", "316140",
    "018260", "009150", "066570", "034730", "003550",
]


def trading_dates(max_days: int = MAX_DAYS) -> list:
    dates, cur = [], datetime.today()
    while len(dates) < max_days:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates


def resolve_col(df: pd.DataFrame, key: str):
    for c in COL_CANDIDATES.get(key, []):
        if c in df.columns:
            return c
    return None


def to_num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return (series.astype(str)
                  .str.replace(",", "", regex=False)
                  .pipe(pd.to_numeric, errors="coerce")
                  .fillna(default))


def add_placeholders(df: pd.DataFrame, date: str) -> pd.DataFrame:
    df["foreign_net"] = 0
    df["inst_net"]    = 0
    df["dart_score"]  = 0
    df["date"]        = date
    return df


def fetch_fdr_cache():
    for date in trading_dates():
        url = FDR_CACHE_URL.format(date=date)
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"  [FDR-CACHE] {date} HTTP {r.status_code} → skip")
                continue

            raw = pd.read_csv(io.StringIO(r.text), dtype=str)
            if len(raw) < MIN_STOCKS:
                print(f"  [FDR-CACHE] {date} 종목 부족({len(raw)}) → skip")
                continue

            col_code   = resolve_col(raw, "code")
            col_close  = resolve_col(raw, "close")
            col_volume = resolve_col(raw, "volume")

            if not col_code or not col_close or not col_volume:
                print(f"  [FDR-CACHE] {date} 필수 컬럼 없음 → skip")
                continue

            col_name   = resolve_col(raw, "name")
            col_market = resolve_col(raw, "market")
            col_open   = resolve_col(raw, "open")
            col_high   = resolve_col(raw, "high")
            col_low    = resolve_col(raw, "low")

            df = pd.DataFrame()
            df["code"]   = raw[col_code].astype(str).str.zfill(6)
            df["name"]   = raw[col_name].fillna("") if col_name   else ""
            df["market"] = raw[col_market]           if col_market else ""
            df["close"]  = to_num(raw[col_close])
            df["volume"] = to_num(raw[col_volume]).astype(int)
            df["open"]   = to_num(raw[col_open])  if col_open  else 0.0
            df["high"]   = to_num(raw[col_high])  if col_high  else 0.0
            df["low"]    = to_num(raw[col_low])   if col_low   else 0.0

            df = df[df["close"]  > 0]
            df = df[df["volume"] > 0]
            df = df.drop_duplicates("code").reset_index(drop=True)

            if len(df) < MIN_STOCKS:
                continue

            print(f"  [1순위 FDR-CACHE] 성공: {date}  {len(df)}종목")
            return add_placeholders(df, date), date, "fdr_cache"

        except Exception as e:
            print(f"  [FDR-CACHE] {date} 오류: {e}")

    return None, None, None


def fetch_fdr_direct():
    try:
        import FinanceDataReader as fdr

        date   = trading_dates()[0]
        frames = []

        for market in ["KOSPI", "KOSDAQ"]:
            try:
                listing = fdr.StockListing(market)
                if listing is None or listing.empty:
                    continue

                listing = listing.reset_index()
                listing.columns = [c.strip() for c in listing.columns]

                col_map = {}
                for col in listing.columns:
                    cl = col.lower()
                    if cl in ["code", "symbol", "ticker"]:
                        col_map[col] = "code"
                    elif cl in ["name", "corp_name", "company"]:
                        col_map[col] = "name"
                    elif cl in ["close", "price", "현재가"]:
                        col_map[col] = "close"
                    elif cl in ["volume", "거래량"]:
                        col_map[col] = "volume"

                listing = listing.rename(columns=col_map)
                listing["market"] = market
                frames.append(listing)

            except Exception as e:
                print(f"  [FDR-DIRECT] {market} 실패: {e}")

        if not frames:
            return None, None, None

        df = pd.concat(frames, ignore_index=True)

        if "code" not in df.columns or "close" not in df.columns:
            return None, None, None

        df["code"]   = df["code"].astype(str).str.zfill(6)
        df["close"]  = pd.to_numeric(df["close"],  errors="coerce").fillna(0)
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        df["name"]   = df["name"].fillna("") if "name" in df.columns else ""
        df["open"]   = pd.to_numeric(df["open"],  errors="coerce").fillna(0) if "open"  in df.columns else 0.0
        df["high"]   = pd.to_numeric(df["high"],  errors="coerce").fillna(0) if "high"  in df.columns else 0.0
        df["low"]    = pd.to_numeric(df["low"],   errors="coerce").fillna(0) if "low"   in df.columns else 0.0

        df = df[df["close"]  > 0]
        df = df[df["volume"] > 0]
        df = df.drop_duplicates("code").reset_index(drop=True)

        if len(df) < MIN_STOCKS:
            return None, None, None

        print(f"  [2순위 FDR-DIRECT] 성공: {len(df)}종목")
        return add_placeholders(df, date), date, "fdr_direct"

    except ImportError:
        print("  [FDR-DIRECT] finance-datareader 미설치")
        return None, None, None
    except Exception as e:
        print(f"  [FDR-DIRECT] 실패: {e}")
        return None, None, None


def fetch_placeholder():
    date = datetime.today().strftime("%Y-%m-%d")
    df   = pd.DataFrame({
        "code":   FALLBACK_CODES,
        "name":   [""] * len(FALLBACK_CODES),
        "market": ["KOSPI"] * len(FALLBACK_CODES),
        "close":  [0.0] * len(FALLBACK_CODES),
        "volume": [0]   * len(FALLBACK_CODES),
        "open":   [0.0] * len(FALLBACK_CODES),
        "high":   [0.0] * len(FALLBACK_CODES),
        "low":    [0.0] * len(FALLBACK_CODES),
    })
    print(f"  [3순위 PLACEHOLDER] {len(df)}종목 — 파이프라인 생존 모드")
    return add_placeholders(df, date), date, "placeholder"


def save_data(df: pd.DataFrame, date: str, source: str) -> None:
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)
        print(f"  [BACKUP] {BACKUP_FILE}")

    payload = {
        "date":   date,
        "source": source,
        "count":  len(df),
        "all":    df.to_dict("records"),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"  [SAVE] {DATA_FILE}  →  {len(df)}종목  (source={source})")


if __name__ == "__main__":
    print("[UNIVERSE BUILD START]")

    df, date, source = fetch_fdr_cache()

    if df is None:
        df, date, source = fetch_fdr_direct()

    if df is None:
        df, date, source = fetch_placeholder()

    save_data(df, date, source)
    print(f"[UNIVERSE BUILD DONE]  source={source}  count={len(df)}")
