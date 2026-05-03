import os
import requests
import pandas as pd
import json
from datetime import datetime, timedelta

KRX_API_KEY = os.environ.get("KRX_API_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY")

def fetch_krx_supply(date_str):
    """KRX OpenAPI로 외국인/기관 순매수 + 주가 가져오기 - IP 직접 호출 버전"""
    url = "https://211.219.116.38/api/stock/investorTradingBySecurities"
    params = {
        "key": KRX_API_KEY,
        "type": "json", 
        "basDd": date_str.replace("-", "")
    }
    headers = {
        "Host": "opendata.krx.co.kr",
        "User-Agent": "Mozilla/5.0"
    }
    
    resp = requests.get(url, params=params, headers=headers, timeout=20, verify=True)
    data = resp.json()
    
    if data.get("outblock", {}).get("data") is None:
        raise Exception(f"KRX API 응답 없음: {data}")
    
    df = pd.DataFrame(data['outblock']['data'])
    df['code'] = df['isuCd'].str[-6:].str.zfill(6)
    df['foreign_net'] = pd.to_numeric(df['frgnNetBuyAmt'], errors='coerce').fillna(0)
    df['inst_net'] = pd.to_numeric(df['orgnNetBuyAmt'], errors='coerce').fillna(0)
    df['close'] = pd.to_numeric(df['tddClsprc'], errors='coerce')
    df['volume'] = pd.to_numeric(df['accTrdvol'], errors='coerce')
    df['name'] = df['isuNm']
    df['market'] = df['mktId']
    df['open'] = pd.to_numeric(df['tddOpnprc'], errors='coerce')
    df['high'] = pd.to_numeric(df['tddHghprc'], errors='coerce') 
    df['low'] = pd.to_numeric(df['tddLowprc'], errors='coerce')
    
    return df[['code','name','market','close','volume','foreign_net','inst_net','open','high','low']]

def fetch_dart_scores(codes, date_str):
    """DART 공시 점수"""
    if not DART_API_KEY:
        print(" [DART] API 키 없음 → skip")
        return {code: 0.0 for code in codes}
    
    try:
        end = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = (end - timedelta(days=20)).strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": start,
            "end_de": end_s,
            "page_count": 100,
        }
        data = requests.get(url, params=params, timeout=10).json()

        if data.get("status") != "000":
            print(f" [DART] status={data.get('status')} → skip")
            return {code: 0.0 for code in codes}

        dart_df = pd.DataFrame(data.get("list", []))
        if dart_df.empty:
            return {code: 0.0 for code in codes}
            
        dart_df = dart_df[dart_df["stock_code"].notna()]
        dart_df["code"] = dart_df["stock_code"].astype(str).str.zfill(6)
        
        positive = ["배당결정", "자사주취득", "영업이익증가", "수주계약", "흑자전환", "실적개선", "매출증가"]
        negative = ["소송제기", "영업손실", "적자전환", "계약해지", "불성실공시", "횡령", "배임"]
        
        def score_report(title: str) -> float:
            pos = sum(1 for k in positive if k in str(title))
            neg = sum(1 for k in negative if k in str(title))
            return float(pos - neg)

        dart_df["dart_score"] = dart_df["report_nm"].apply(score_report)
        result = dart_df.groupby("code")["dart_score"].mean().to_dict()
        return {code: result.get(code, 0.0) for code in codes}
        
    except Exception as e:
        print(f" [DART] 오류: {e}")
        return {code: 0.0 for code in codes}

def fetch_fdr_universe():
    """KRX API로 유니버스 구성"""
    date_str = datetime.today().strftime("%Y-%m-%d")
    print(f"[KRX] {date_str} 수급 데이터 가져오는 중...")
    
    df = fetch_krx_supply(date_str)
    print(f"[KRX] {len(df)}종목 로드 완료")
    
    dart_map = fetch_dart_scores(df['code'].tolist(), date_str)
    df['dart_score'] = df['code'].map(dart_map).fillna(0.0)
    
    return df, date_str, "krx-openapi"

if __name__ == "__main__":
    df, date_str, source = fetch_fdr_universe()
    out = {"date": date_str, "source": source, "all": df.to_dict(orient="records")}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[DATA] data.json 저장 완료 / {len(df)}개 종목")
