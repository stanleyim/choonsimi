"""
fetch_data.py — Universe Builder v5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
종가/거래량 (3단계 fallback):
  1순위: FDR GitHub 캐시 CSV
  2순위: FinanceDataReader 직접
  3순위: 0값 placeholder

수급 (foreign_net / inst_net):
  1순위: KRX data.krx.co.kr OTP 방식 (전종목 1회 호출, 20일 합산)
  2순위: 0 유지 (파이프라인 생존)

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
FLOW_DAYS   = 20   # 수급 집계 기간 (영업일 기준)

FDR_CACHE_URL = (
    "https://raw.githubusercontent.com/"
    "FinanceData/fdr_krx_data_cache/"
    "refs/heads/master/data/listing/krx/{date}.csv"
)

KRX_OTP_URL  = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_DOWN_URL = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
KRX_REFERER  = "http://data.krx.co.kr"

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UTIL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def trading_dates(max_days: int = MAX_DAYS) -> list:
    """오늘부터 소급 평일 날짜 리스트."""
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
    """engine.py가 기대하는 placeholder 컬럼 추가."""
    for col in ["foreign_net", "inst_net"]:
        if col not in df.columns:
            df[col] = 0
    df["dart_score"] = 0
    df["date"]       = date
    return df


def biz_date_range(ref_date: str, days: int = FLOW_DAYS):
    """ref_date 기준 days 영업일 전 날짜 반환 (여유분 1.5배)."""
    end = datetime.strptime(ref_date, "%Y-%m-%d")
    # 영업일 부족분 대비 1.5배 여유
    start = end - timedelta(days=int(days * 1.5))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KRX OTP 수급 수집 (전종목 1회 호출)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_krx_flow_market(mkt_id: str, start: str, end: str) -> pd.DataFrame:
    """
    KRX 투자자별 거래실적 (기간 합산, 전종목).
    mkt_id: STK=KOSPI, KSQ=KOSDAQ
    OTP 방식: generate.cmd → download.cmd
    인증 불필요, 무료.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer":    KRX_REFERER,
    })

    # Step 1: OTP 발급
    otp_params = {
        "url":          "dbms/MDC/STAT/standard/MDCSTAT02303",
        "mktId":        mkt_id,
        "strtDd":       start,
        "endDd":        end,
        "money":        "1",
        "csvxls_isNo":  "false",
        "name":         "fileDown",
    }

    otp_resp = session.post(KRX_OTP_URL, data=otp_params, timeout=TIMEOUT)
    otp = otp_resp.text.strip()

    if not otp:
        print(f"  [KRX-OTP] {mkt_id} OTP 발급 실패")
        return pd.DataFrame()

    # Step 2: CSV 다운로드
    down_resp = session.post(
        KRX_DOWN_URL,
        data={"code": otp},
        timeout=TIMEOUT,
    )

    # EUC-KR / UTF-8 인코딩 자동 감지
    for enc in ["euc-kr", "utf-8", "cp949"]:
        try:
            text = down_resp.content.decode(enc)
            df   = pd.read_csv(io.StringIO(text))
            if len(df) > 0:
                break
        except Exception:
            continue
    else:
        print(f"  [KRX-OTP] {mkt_id} CSV 파싱 실패")
        return pd.DataFrame()

    print(f"  [KRX-OTP] {mkt_id} 수신: {len(df)}행  컬럼: {df.columns.tolist()}")
    return df


def parse_krx_flow(df: pd.DataFrame) -> pd.DataFrame:
    """
    KRX 수급 CSV → code / foreign_net / inst_net 추출.
    컬럼명은 KRX 버전에 따라 다를 수 있어 후보 리스트로 유연 처리.
    """
    if df.empty:
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    # 종목코드 컬럼 후보
    code_candidates = ["종목코드", "티커", "ISU_SRT_CD", "Code", "code"]
    # 외국인 순매수 후보
    foreign_candidates = [
        "외국인_순매수", "외국인순매수", "외국인_순매수금액",
        "외인순매수", "Foreigners", "Foreign"
    ]
    # 기관합계 순매수 후보
    inst_candidates = [
        "기관합계_순매수", "기관합계순매수", "기관합계_순매수금액",
        "기관순매수", "기관합계", "Institutions", "Institution"
    ]

    col_code    = next((c for c in code_candidates    if c in df.columns), None)
    col_foreign = next((c for c in foreign_candidates if c in df.columns), None)
    col_inst    = next((c for c in inst_candidates    if c in df.columns), None)

    if not col_code:
        print(f"  [KRX-OTP] 종목코드 컬럼 없음 → skip")
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    out = pd.DataFrame()
    out["code"] = df[col_code].astype(str).str.zfill(6)

    out["foreign_net"] = to_num(df[col_foreign]) if col_foreign else 0.0
    out["inst_net"]    = to_num(df[col_inst])    if col_inst    else 0.0

    return out[["code", "foreign_net", "inst_net"]]


def fetch_krx_flow(ref_date: str) -> pd.DataFrame:
    """
    KOSPI + KOSDAQ 수급 합산.
    실패 시 빈 DataFrame 반환 (0 유지).
    """
    start, end = biz_date_range(ref_date)
    print(f"  [KRX-OTP] 수급 수집: {start} ~ {end}")

    frames = []
    for mkt_id in ["STK", "KSQ"]:
        try:
            raw = fetch_krx_flow_market(mkt_id, start, end)
            if not raw.empty:
                parsed = parse_krx_flow(raw)
                if not parsed.empty:
                    frames.append(parsed)
        except Exception as e:
            print(f"  [KRX-OTP] {mkt_id} 오류: {e}")

    if not frames:
        print("  [KRX-OTP] 전체 실패 → 수급 0 유지")
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    result = pd.concat(frames, ignore_index=True)
    result = result.groupby("code")[["foreign_net", "inst_net"]].sum().reset_index()

    success = (result["foreign_net"] != 0).sum()
    print(f"  [KRX-OTP] 완료: {len(result)}종목 / 수급 확인 {success}종목")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1순위: FDR GitHub 캐시 CSV (종가/거래량)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2순위: FinanceDataReader 직접
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3순위: placeholder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENTRY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("[UNIVERSE BUILD START]")

    # ── 종가/거래량 수집 ───────────────────────
    df, date, source = fetch_fdr_cache()

    if df is None:
        df, date, source = fetch_fdr_direct()

    if df is None:
        df, date, source = fetch_placeholder()

    # ── 수급 수집 (placeholder 제외) ──────────
    if source != "placeholder":
        try:
            flow_df = fetch_krx_flow(date)
            if not flow_df.empty:
                df = df.merge(flow_df, on="code", how="left")
                df["foreign_net"] = df["foreign_net"].fillna(0)
                df["inst_net"]    = df["inst_net"].fillna(0)
                print(f"  [FLOW] 수급 병합 완료")
            else:
                print(f"  [FLOW] 수급 없음 → 0 유지")
        except Exception as e:
            print(f"  [FLOW] 수급 수집 실패 → 0 유지: {e}")

    save_data(df, date, source)
    print(f"[UNIVERSE BUILD DONE]  source={source}  count={len(df)}")
