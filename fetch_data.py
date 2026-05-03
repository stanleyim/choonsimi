import os
import requests
import pandas as pd
import json
from datetime import datetime, timedelta
import time

DART_API_KEY = os.environ.get("DART_API_KEY")

def fetch_krx_ip(date_str):
    """1순위: KRX IP 직접 호출"""
    url = "https://211.219.116.38/api/stock/investorTradingBySecurities"
    params = {"key": os.environ.get("KRX_API_KEY"), "type": "json", "basDd": date_str.replace("-", "")}
    headers = {"Host": "opendata.krx.co.kr", "User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()
    if data.get("outblock", {}).get("data") is None:
        raise Exception(f"KRX 응답 없음: {data}")
    df = pd.DataFrame(data['outblock']['data'])
    df['code'] = df['isuCd'].str[-6:].str.zfill(6)
    df['foreign_net'] = pd.to_numeric(df['frgnNetBuyAmt'], errors='coerce').fillna(0)
    df['inst_net'] = pd.to_numeric(df['orgnNetBuyAmt'], errors='coerce').fillna(0)
    df['close'] = pd.to_numeric(df['tddClsprc'], errors='coerce')
    df['volume'] = pd.to_numeric(df['accTrdvol'], errors='coerce')
    df['name'] = df['isuNm']
    df['market'] = df['mktId']
    return df[['code','name','market','close','volume','foreign_net','inst_net']]

def fetch_fdr(date_str):
    """2순위: FinanceDataReader 일괄 조회 - 올바른 함수 사용"""
    import FinanceDataReader as fdr
    
    # KOSPI + KOSDAQ 일괄 조회
    kospi = fdr.DataReader('KS11', date_str, date_str) # KOSPI 지수
    kosdaq = fdr.DataReader('KQ11', date_str, date_str) # KOSDAQ 지수
    
    # 종목별 일괄 조회
    listing = pd.concat([fdr.StockListing('KOSPI'), fdr.StockListing('KOSDAQ')])
    codes = listing['Code'].astype(str).str.zfill(6).tolist()
    
    supply_list = []
    for code in codes:
        try:
            d = fdr.daily(code, date_str, date_str) # 올바른 함수: daily
            if not d.empty:
                supply_list.append({
                    'code': code,
                    'name': listing.loc[listing['Code']==code, 'Name'].values[0],
                    'market': listing.loc[listing['Code']==code, 'Market'].values[0],
                    'close': float(d['Close'].iloc[-1]),
                    'volume': float(d['Volume'].iloc[-1]),
                    'foreign_net': float(d['ForeignNetPurchase'].iloc[-1]),
                    'inst_net': float(d['InstitutionNetPurchase'].iloc[-1])
                })
        except:
            continue
    
    return pd.DataFrame(supply_list)

def fetch_sample(date_str):
    """3순위: 샘플 데이터"""
    print(" 샘플 데이터 생성")
    return pd.DataFrame({
        'code': ['005930','000660','035420'],
        'name': ['삼성전자','SK하이닉스','NAVER'], 
        'market': ['KOSPI','KOSPI','KOSDAQ'],
        'close': [75000, 180000, 220000],
        'volume': [10000000, 3000000, 1500000],
        'foreign_net': [500000, -200000, 100000],
        'inst_net': [-300000, 400000, -50000000]
    })

def fetch_fdr_universe():
    date_str = datetime.today().strftime("%Y-%m-%d")
    print(f"[DATA] {date_str} 데이터 수집 시작...")
    
    # 1순위: KRX IP
    try:
        df = fetch_krx_ip(date_str)
        print(f"[KRX] {len(df)}종목 로드 완료")
        source = "krx-ip"
    except Exception as e:
        print(f" KRX 실패: {e}")
        
        # 2순위: FDR
        try:
            df = fetch_fdr(date_str) 
            print(f"[FDR] {len(df)}종목 로드 완료")
            source = "finance-datareader"
        except Exception as e:
            print(f" FDR 실패: {e}")
            
            # 3순위: 샘플
            df = fetch_sample(date_str)
            source = "sample"
    
    # DART 공시 점수
    if DART_API_KEY and source!= "sample":
        try:
            end = datetime.strptime(date_str, "%Y-%m-%d")
            start = (end - timedelta(days=20)).strftime("%Y%m%d")
            end_s = end.strftime("%Y%m%d")
            url = "https://opendart.fss.or.kr/api/list.json"
            params = {"crtfc_key": DART_API_KEY, "bgn_de": start, "end_de": end_s, "page_count": 100}
            data = requests.get(url, params=params, timeout=10).json()
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
                dart_map = dart_df.groupby("code")["report_nm"].apply(lambda x: sum(score_report(t) for t in x)).to_dict()
                df['dart_score'] = df['code'].map(dart_map).fillna(0.0)
            else:
                df['dart_score'] = 0.0
        except:
            df['dart_score'] = 0.0
    else:
        df['dart_score'] = 0.0
    
    df['open'] = df['close'] * 0.99
    df['high'] = df['close'] * 1.02 
    df['low'] = df['close'] * 0.98
    
    return df[['code','name','market','close','volume','foreign_net','inst_net','open','high','low','dart_score']], date_str, source

if __name__ == "__main__":
    df, date_str, source = fetch_fdr_universe()
    out = {"date": date_str, "source": source, "all": df.to_dict(orient="records")}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[DATA] data.json 저장 완료 / {len(df)}개 종목 / source={source}")
