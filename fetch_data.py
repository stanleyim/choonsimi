import os
import requests
import pandas as pd
import json
from datetime import datetime, timedelta
import FinanceDataReader as fdr
import time

DART_API_KEY = os.environ.get("DART_API_KEY")

def fetch_fdr_universe():
    date_str = datetime.today().strftime("%Y-%m-%d")
    print(f"[FDR] {date_str} 수급 데이터 가져오는 중...")
    
    # 1. KOSPI + KOSDAQ 종목 리스트 한번에 가져오기
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")
    listing = pd.concat([kospi, kosdaq])
    print(f"[FDR] {len(listing)}종목 유니버스 로드 완료")
    
    # 2. 일괄 수급 데이터 - KRX 일별 수급 전체 조회
    try:
        # KOSPI 전체 일괄 조회
        kospi_supply = fdr.StockMarketDaily(date_str, date_str, market='KOSPI')
        kosdaq_supply = fdr.StockMarketDaily(date_str, date_str, market='KOSDAQ') 
        supply = pd.concat([kospi_supply, kosdaq_supply])
        print(f"[FDR] {len(supply)}종목 수급 로드 완료")
    except Exception as e:
        print(f" FDR 수급 조회 실패: {e}")
        # 재시도 1회
        time.sleep(5)
        kospi_supply = fdr.StockMarketDaily(date_str, date_str, market='KOSPI')
        kosdaq_supply = fdr.StockMarketDaily(date_str, date_str, market='KOSDAQ')
        supply = pd.concat([kospi_supply, kosdaq_supply])
    
    # 3. 병합
    df = listing.merge(supply, left_on='Code', right_on='Code', how='inner')
    df['code'] = df['Code'].astype(str).str.zfill(6)
    df['foreign_net'] = df['Foreign'].fillna(0)
    df['inst_net'] = df['Institution'].fillna(0)
    df['close'] = df['Close']
    df['volume'] = df['Volume']
    df['name'] = df['Name']
    df['market'] = df['Market']
    df['open'] = df['Open']
    df['high'] = df['High'] 
    df['low'] = df['Low']
    
    # 4. DART 공시 점수
    if DART_API_KEY:
        try:
            end = datetime.strptime(date_str, "%Y-%m-%d")
            start = (end - timedelta(days=20)).strftime("%Y%m%d")
            end_s = end.strftime("%Y%m%d")
            url = "https://opendart.fss.or.kr/api/list.json"
            params = {"crtfc_key": DART_API_KEY, "bgn_de": start, "end_de": end_s, "page_count": 100}
            data = requests.get(url, params=params, timeout=15).json()
            
            if data.get("status") == "000" and data.get("list"):
                dart_df = pd.DataFrame(data["list"])
                dart_df = dart_df[dart_df["stock_code"].notna()]
                dart_df["code"] = dart_df["stock_code"].astype(str).str.zfill(6)
                
                positive = ["배당결정", "자사주취득", "영업이익증가", "수주계약", "흑자전환"]
                negative = ["소송제기", "영업손실", "적자전환", "계약해지", "불성실공시"]
                def score_report(title): 
                    pos = sum(1 for k in positive if k in str(title))
                    neg = sum(1 for k in negative if k in str(title))
                    return float(pos - neg)
                
                dart_df["dart_score"] = dart_df["report_nm"].apply(score_report)
                dart_map = dart_df.groupby("code")["dart_score"].mean().to_dict()
                df['dart_score'] = df['code'].map(dart_map).fillna(0.0)
            else:
                df['dart_score'] = 0.0
        except Exception as e:
            print(f" DART 오류: {e}")
            df['dart_score'] = 0.0
    else:
        df['dart_score'] = 0.0
    
    return df[['code','name','market','close','volume','foreign_net','inst_net','open','high','low','dart_score']], date_str, "finance-datareader"

if __name__ == "__main__":
    df, date_str, source = fetch_fdr_universe()
    out = {"date": date_str, "source": source, "all": df.to_dict(orient="records")}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[DATA] data.json 저장 완료 / {len(df)}개 종목")
