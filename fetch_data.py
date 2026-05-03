"""
fetch_data.py — Universe Builder v5.1 FINAL
종가/거래량 (3단계 fallback) + 수급 (KRX OTP 방식)
"""

import io
import json
import os
import shutil
from datetime import datetime, timedelta

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "data.json")
BACKUP_FILE = os.path.join(ROOT, "data.json.bak")

MIN_STOCKS = 50
MAX_DAYS = 10
TIMEOUT = 30
FLOW_DAYS = 20

FDR_CACHE_URL = (
    "https://raw.githubusercontent.com/"
    "FinanceData/fdr_krx_data_cache/"
    "refs/heads/master/data/listing/krx/{date}.csv"
)

KRX_OTP_URL = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_DOWN_URL = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
KRX_REFERER = "http://data.krx.co.kr/"

KRX_COL_CANDIDATES = {
    "code": ["종목코드", "티커", "ISU_SRT_CD", "Code", "code", "symbol"],
    "name": ["종목명", "종목이름", "ISU_ABBRV", "Name", "name"],
    "foreign_net": [
        "외국인순매수", "외국인_순매수", "외국인 순매수",
        "외국인순매수금액", "외국인_순매수금액",
        "외인순매수", "외인_순매수",
        "Foreigners_Net", "Foreign_Net", "Foreigners", "Foreign",
        "외국인순매수(천원)", "외국인순매수(백만원)",
    ],
    "inst_net": [
        "기관합계순매수", "기관합계_순매수", "기관합계 순매수",
        "기관순매수", "기관_순매수", "기관 순매수",
        "기관합계순매수금액", "기관합계_순매수금액",
        "기관투자자순매수", "기관투자자_순매수",
        "Institutions_Net", "Institution_Net", "Institutions", "Institution",
        "기관합계(천원)", "기관합계(백만원)",    ],
}

COL_CANDIDATES = {
    "code": ["Code", "Symbol", "code", "symbol", "티커"],
    "name": ["Name", "ISU_ABBRV", "name", "종목명"],
    "market": ["Market", "MarketId", "market", "시장구분"],
    "close": ["Close", "TDD_CLSPRC", "close", "종가"],
    "volume": ["Volume", "ACC_TRDVOL", "volume", "거래량"],
    "open": ["Open", "TDD_OPNPRC", "open", "시가"],
    "high": ["High", "TDD_HGPRC", "high", "고가"],
    "low": ["Low", "TDD_LWPRC", "low", "저가"],
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


def resolve_col(df: pd.DataFrame, key: str, candidates: dict) -> str | None:
    for c in candidates.get(key, []):
        if c in df.columns:
            return c
    return None


def to_num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("(", "-", regex=False)
        .str.replace(")", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(default)
    )


def add_placeholders(df: pd.DataFrame, date: str) -> pd.DataFrame:    for col in ["foreign_net", "inst_net"]:
        if col not in df.columns:
            df[col] = 0
    df["dart_score"] = 0
    df["date"] = date
    return df


def biz_date_range(ref_date: str, days: int = FLOW_DAYS):
    end = datetime.strptime(ref_date, "%Y-%m-%d")
    start = end - timedelta(days=int(days * 1.5))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_krx_flow_market(mkt_id: str, start: str, end: str) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": KRX_REFERER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    otp_params = {
        "url": "dbms/MDC/STAT/standard/MDCSTAT02303",
        "mktId": mkt_id,
        "strtDd": start,
        "endDd": end,
        "money": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
    }

    try:
        otp_resp = session.post(KRX_OTP_URL, data=otp_params, timeout=TIMEOUT)
        otp = otp_resp.text.strip()

        if not otp:
            print(f"  [KRX-OTP] {mkt_id} OTP 발급 실패 (응답: {otp_resp.status_code})")
            return pd.DataFrame()

        down_resp = session.post(KRX_DOWN_URL, data={"code": otp}, timeout=TIMEOUT)

        for enc in ["euc-kr", "cp949", "utf-8", "utf-8-sig"]:
            try:
                text = down_resp.content.decode(enc)
                df = pd.read_csv(io.StringIO(text))
                if len(df) > 0:
                    return df
            except Exception:
                continue
        print(f"  [KRX-OTP] {mkt_id} CSV 파싱 실패 (인코딩 불일치)")
        return pd.DataFrame()

    except requests.exceptions.Timeout:
        print(f"  [KRX-OTP] {mkt_id} 타임아웃 ({TIMEOUT}초)")
        return pd.DataFrame()
    except Exception as e:
        print(f"  [KRX-OTP] {mkt_id} 오류: {type(e).__name__}: {e}")
        return pd.DataFrame()


def parse_krx_flow(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    col_code = resolve_col(df, "code", KRX_COL_CANDIDATES)
    col_foreign = resolve_col(df, "foreign_net", KRX_COL_CANDIDATES)
    col_inst = resolve_col(df, "inst_net", KRX_COL_CANDIDATES)

    if col_code is None or col_foreign is None or col_inst is None:
        print(f"  [KRX-DEBUG] 실제 컬럼: {df.columns.tolist()}")
        if len(df) > 0:
            sample = df.iloc[0].to_dict()
            print(f"  [KRX-DEBUG] 샘플 1행: {sample}")

    if not col_code:
        print(f"  [KRX-OTP] 종목코드 컬럼 없음 → skip")
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    out = pd.DataFrame()
    out["code"] = df[col_code].astype(str).str.zfill(6)
    out["foreign_net"] = to_num(df[col_foreign]) if col_foreign else 0.0
    out["inst_net"] = to_num(df[col_inst]) if col_inst else 0.0

    return out[["code", "foreign_net", "inst_net"]]


def fetch_krx_flow(ref_date: str) -> pd.DataFrame:
    start, end = biz_date_range(ref_date)
    print(f"  [KRX-OTP] 수급 수집: {start} ~ {end}")

    frames = []
    for mkt_id in ["STK", "KSQ"]:
        try:
            raw = fetch_krx_flow_market(mkt_id, start, end)
            if not raw.empty:
                parsed = parse_krx_flow(raw)
                if not parsed.empty:
                    frames.append(parsed)                    print(f"  [KRX-OTP] {mkt_id} 파싱 성공: {len(parsed)}종목")
                else:
                    print(f"  [KRX-OTP] {mkt_id} 파싱 실패: 컬럼 매칭 불가")
            else:
                print(f"  [KRX-OTP] {mkt_id} 데이터 없음")
        except Exception as e:
            print(f"  [KRX-OTP] {mkt_id} 오류: {type(e).__name__}: {e}")

    if not frames:
        print("  [KRX-OTP] 전체 실패 → 수급 0 유지")
        return pd.DataFrame(columns=["code", "foreign_net", "inst_net"])

    result = pd.concat(frames, ignore_index=True)
    result = result.groupby("code")[["foreign_net", "inst_net"]].sum().reset_index()

    success = (result["foreign_net"].abs() > 0).sum()
    print(f"  [KRX-OTP] 완료: {len(result)}종목 / 수급 확인 {success}종목")
    return result


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

            col_code = resolve_col(raw, "code", COL_CANDIDATES)
            col_close = resolve_col(raw, "close", COL_CANDIDATES)
            col_volume = resolve_col(raw, "volume", COL_CANDIDATES)

            if not col_code or not col_close or not col_volume:
                print(f"  [FDR-CACHE] {date} 필수 컬럼 없음 → skip")
                continue

            col_name = resolve_col(raw, "name", COL_CANDIDATES)
            col_market = resolve_col(raw, "market", COL_CANDIDATES)
            col_open = resolve_col(raw, "open", COL_CANDIDATES)
            col_high = resolve_col(raw, "high", COL_CANDIDATES)
            col_low = resolve_col(raw, "low", COL_CANDIDATES)

            df = pd.DataFrame()
            df["code"] = raw[col_code].astype(str).str.zfill(6)            df["name"] = raw[col_name].fillna("") if col_name else ""
            df["market"] = raw[col_market] if col_market else ""
            df["close"] = to_num(raw[col_close])
            df["volume"] = to_num(raw[col_volume]).astype(int)
            df["open"] = to_num(raw[col_open]) if col_open else 0.0
            df["high"] = to_num(raw[col_high]) if col_high else 0.0
            df["low"] = to_num(raw[col_low]) if col_low else 0.0

            df = df[df["close"] > 0]
            df = df[df["volume"] > 0]
            df = df.drop_duplicates("code").reset_index(drop=True)

            if len(df) < MIN_STOCKS:
                continue

            print(f"  [1순위 FDR-CACHE] 성공: {date}  {len(df)}종목")
            return add_placeholders(df, date), date, "fdr_cache"

        except Exception as e:
            print(f"  [FDR-CACHE] {date} 오류: {type(e).__name__}: {e}")

    return None, None, None


def fetch_fdr_direct():
    try:
        import FinanceDataReader as fdr

        date = trading_dates()[0]
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
                    elif cl in ["volume", "거래량"]:                        col_map[col] = "volume"

                listing = listing.rename(columns=col_map)
                listing["market"] = market
                frames.append(listing)

            except Exception as e:
                print(f"  [FDR-DIRECT] {market} 실패: {type(e).__name__}: {e}")

        if not frames:
            return None, None, None

        df = pd.concat(frames, ignore_index=True)

        if "code" not in df.columns or "close" not in df.columns:
            return None, None, None

        df["code"] = df["code"].astype(str).str.zfill(6)
        df["close"] = pd.to_numeric(df["close"], errors="coerce").fillna(0)
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        df["name"] = df["name"].fillna("") if "name" in df.columns else ""
        df["open"] = pd.to_numeric(df["open"], errors="coerce").fillna(0) if "open" in df.columns else 0.0
        df["high"] = pd.to_numeric(df["high"], errors="coerce").fillna(0) if "high" in df.columns else 0.0
        df["low"] = pd.to_numeric(df["low"], errors="coerce").fillna(0) if "low" in df.columns else 0.0

        df = df[df["close"] > 0]
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
        print(f"  [FDR-DIRECT] 실패: {type(e).__name__}: {e}")
        return None, None, None


def fetch_placeholder():
    date = datetime.today().strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "code": FALLBACK_CODES,
        "name": [""] * len(FALLBACK_CODES),
        "market": ["KOSPI"] * len(FALLBACK_CODES),
        "close": [0.0] * len(FALLBACK_CODES),        "volume": [0] * len(FALLBACK_CODES),
        "open": [0.0] * len(FALLBACK_CODES),
        "high": [0.0] * len(FALLBACK_CODES),
        "low": [0.0] * len(FALLBACK_CODES),
    })
    print(f"  [3순위 PLACEHOLDER] {len(df)}종목 — 파이프라인 생존 모드")
    return add_placeholders(df, date), date, "placeholder"


def save_data(df: pd.DataFrame, date: str, source: str) -> None:
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)
        print(f"  [BACKUP] {BACKUP_FILE}")

    payload = {
        "date": date,
        "source": source,
        "count": len(df),
        "all": df.to_dict("records"),
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

    if source != "placeholder":
        try:
            flow_df = fetch_krx_flow(date)
            if not flow_df.empty:
                df = df.merge(flow_df, on="code", how="left")
                df["foreign_net"] = df["foreign_net"].fillna(0)
                df["inst_net"] = df["inst_net"].fillna(0)
                print(f"  [FLOW] 수급 병합 완료")
            else:
                print(f"  [FLOW] 수급 없음 → 0 유지")
        except Exception as e:
            print(f"  [FLOW] 수급 수집 실패 → 0 유지: {type(e).__name__}: {e}")
    save_data(df, date, source)
    print(f"[UNIVERSE BUILD DONE]  source={source}  count={len(df)}")
