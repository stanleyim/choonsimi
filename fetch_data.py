"""
fetch_data.py — v3.7 KIS Production Final
────────────────────────────────────────────────────────────
KIS API → KRX TOP600 30일 일봉 수집
개선: 토큰 선갱신, 1회 재시도, HTTP 상태체크
출력: history.csv, sector_map.json
────────────────────────────────────────────────────────────
"""

import pandas as pd, json, requests, time, os
from datetime import datetime, timezone, timedelta

KST        = timezone(timedelta(hours=9))
OUTPUT_CSV = "history.csv"
SECTOR_JSON = "sector_map.json"
MAX_STOCKS = 600
DAYS_LOOKBACK = 30
DOMAIN = "https://openapi.koreainvestment.com:9443"
TOKEN_REFRESH_INTERVAL = 200

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

if not APP_KEY or not APP_SECRET:
    raise RuntimeError("KIS_APP_KEY, KIS_APP_SECRET 환경변수가 설정되지 않았습니다.")

EXCLUDE_KEYWORDS = ["ETF", "ETN", "리츠", "스팩", "ETC", "인버스", "레버리지"]

def get_token():
    url = f"{DOMAIN}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(url, headers=headers, timeout=30)
    if res.status_code != 200:
        raise RuntimeError(f"토큰 발급 HTTP {res.status_code}")
    data = res.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 발급 실패: {data}")
    return data["access_token"]

def get_stock_list_with_value(token):
    """KRX 전체 종목 + 현재가, 거래량, 거래대금 받아오기"""
    url = f"{DOMAIN}/uapi/domestic-stock/v1/quotations/search-stock-info"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "CTF10076R"
    }
    params = {"PRDT_TYPE_CD": "300"}
    res = requests.get(url, headers=headers, params=params, timeout=30)
    if res.status_code != 200:
        raise RuntimeError(f"종목리스트 HTTP {res.status_code}")
    res = res.json()
    
    stocks = []
    for item in res.get("output", []):
        name = item["stk_nm"]
        code = item["stk_cd"]
        if any(x in name for x in EXCLUDE_KEYWORDS):
            continue
        if item["prdt_type_cd"] not in ["01", "02"]:
            continue
        
        price = int(item.get("stck_prpr", 0))
        volume = int(item.get("acml_vol", 0))
        if price == 0 or volume == 0:
            continue
            
        value = price * volume
        stocks.append({
            "code": code, 
            "name": name, 
            "sector": item.get("induty_nm", "기타") or "기타",
            "price": price,
            "volume": volume,
            "value": value
        })
    return pd.DataFrame(stocks)

def get_daily_price(token, code, start_date, end_date):
    """종목별 30일 일봉 조회 + HTTP 상태체크"""
    url = f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start_date.replace("-", ""),
        "FID_INPUT_DATE_2": end_date.replace("-", ""),
        "FID_PERIOD_DIV_CODE": "D"
    }
    res = requests.get(url, headers=headers, params=params, timeout=30)
    if res.status_code != 200:
        raise RuntimeError(f"HTTP {res.status_code}")
    res = res.json()
    
    output = res.get("output", [])
    if not output:
        return pd.DataFrame()
    
    df = pd.DataFrame(output)
    df = df.rename(columns={
        "stck_bsop_date": "date",
        "stck_oprc": "open", "stck_hgpr": "high", "stck_lwpr": "low",
        "stck_clpr": "close", "acml_vol": "volume"
    })
    df["code"] = code
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(int)
    return df[["date", "code", "open", "high", "low", "close", "volume"]]

def main():
    now       = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    start_str = (now - timedelta(days=DAYS_LOOKBACK)).strftime("%Y-%m-%d")

    print(f"[DATA] {today_str} KRX TOP{MAX_STOCKS} 30일 데이터 수집 시작")

    # 1️⃣ 종목 리스트 + 거래대금 확보
    try:
        token = get_token()
        stocks_df = get_stock_list_with_value(token)
        print(f"[DATA] 활성종목 {len(stocks_df)}개 로드")
    except Exception as e:
        print(f"[ERROR] 종목리스트 조회 실패: {e}")
        return

    # 2️⃣ 거래대금 TOP600 선정
    top600_df = stocks_df.nlargest(MAX_STOCKS, "value")
    top600_codes = top600_df["code"].tolist()
    sector_map = dict(zip(top600_df["code"], top600_df["sector"]))
    print(f"[DATA] 거래대금 TOP{len(top600_codes)}종목 선정")

    # 3️⃣ TOP600 종목별 30일 일봉 조회
    all_history = []
    failed_codes = []
    for i, code in enumerate(top600_codes, 1):
        # 🔴 다음 블록 시작 전에 토큰 미리 갱신
        if (i - 1) % TOKEN_REFRESH_INTERVAL == 0 and i != 1:
            try:
                token = get_token()
                print(f"[TOKEN] {i}번째 종목에서 토큰 재발급")
            except Exception as e:
                print(f"[ERROR] 토큰 재발급 실패: {e}")
                break
        
        # 🔴 1회 재시도 로직
        df = pd.DataFrame()
        for attempt in range(2):
            try:
                df = get_daily_price(token, code, start_str, today_str)
                break
            except Exception as e:
                if attempt == 1:  # 2번째 시도도 실패
                    failed_codes.append(code)
                    print(f"[ERROR] {code} 최종 실패: {e}")
                else:
                    time.sleep(0.2)  # 재시도 전 0.2초 대기
        
        if not df.empty:
            all_history.append(df)
        else:
            if code not in failed_codes:
                failed_codes.append(code)
                print(f" {code} 데이터 없음")
        
        if i % 50 == 0:
            print(f"[PROGRESS] {i}/{len(top600_codes)} 종목 완료")
        time.sleep(0.11)  # KIS 초당 10건 제한

    if not all_history:
        print("[ERROR] 일봉 데이터 수집 실패 → 종료")
        return

    history_df = pd.concat(all_history, ignore_index=True)
    history_df = history_df.drop_duplicates(subset=["code", "date"])
    history_df = history_df.sort_values(["code", "date"])
    
    print(f"[DATA] 총 {len(history_df)}행 저장")
    print(f"[DATA] 실패종목 {len(failed_codes)}개: {failed_codes[:10]}")

    # 4️⃣ 저장
    history_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    with open(SECTOR_JSON, "w", encoding="utf-8") as f:
        json.dump(sector_map, f, indent=2, ensure_ascii=False)

    print(f"[DONE] {OUTPUT_CSV} {len(history_df)}행 저장 완료")
    print(f"[DONE] {SECTOR_JSON} {len(sector_map)}종목 저장 완료")
    print(f"       실제 수집종목: {history_df['code'].nunique()}개 / 목표 {len(top600_codes)}개")
    print(f"       날짜 범위: {history_df['date'].min()} ~ {history_df['date'].max()}")

if __name__ == "__main__":
    main()
