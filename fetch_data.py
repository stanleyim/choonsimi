"""fetch_data.py — FINAL DEBUG VERSION"""
import os, requests, pandas as pd, json, io
from datetime import datetime, timedelta

DART_API_KEY = os.environ.get("DART_API_KEY")

# KRX 컬럼명 후보 - 업데이트 대비
KRX_COL_CANDIDATES = {
    'code': ['종목코드', 'ISU_CD', 'isuCd'],
    'foreign_net': ['외국인순매수', '외국인순매수량', 'frgnNetBuyAmt'],
    'inst_net': ['기관합계순매수', '기관합계순매수량', 'orgnNetBuyAmt'], 
    'close': ['종가', 'TDD_CLSPRC', 'tddClsprc'],
    'volume': ['거래량', 'ACC_TRDVOL', 'accTrdvol'],
    'name': ['종목명', 'ISU_NM', 'isuNm'],
    'market': ['시장구분', 'MKT_ID', 'mktId']
}

def find_col(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    raise KeyError(f"컬럼 없음. 실제 컬럼: {df.columns.tolist()}")

def fetch_krx_otp(date_str):
    url = "https://opendata.krx.co.kr/contents/MDC/MDI/mdiLoader"
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
        "locale": "ko_KR", 
        "share": "1",
        "money": "1", 
        "date": date_str.replace("-", "")
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://opendata.krx.co.kr/"}
    
    print(f" [KRX-DEBUG] 요청 날짜: {date_str}")
    
    resp = requests.post(url, data=payload, headers=headers, timeout=30)
    if resp.status_code!= 200:
        raise Exception(f"KRX HTTP {resp.status_code}")
    
    print(f" [KRX-DEBUG] 응답 미리보기: {resp.text[:200]}...")
    
    # 인코딩 자동 감지 euc-kr/cp949/utf-8
    for enc in ["euc-kr", "cp949", "utf-8", "utf-8-sig"]:
        try:
            df = pd.read_csv(io.StringIO(resp.content.decode(enc)))
            if len(df) > 0:
                break
        except:
            continue
    else:
        raise Exception("KRX CSV 인코딩 감지 실패")
    
    print(f" [KRX-DEBUG] 실제 컬럼: {df.columns.tolist()}")
    if len(df) > 0:
        print(f" [KRX-DEBUG] 샘플 1행: {df.iloc[0].to_dict()}")
    
    if df.empty:
        raise Exception("KRX 데이터 0건")
    
    # 컬럼명 자동 매칭
    col_code = find_col(df, KRX_COL_CANDIDATES['code'])
    col_foreign = find_col(df, KRX_COL_CANDIDATES['foreign_net']) 
    col_inst = find_col(df, KRX_COL_CANDIDATES['inst_net'])
    col_close = find_col(df, KRX_COL_CANDIDATES['close'])
    col_volume = find_col(df, KRX_COL_CANDIDATES['volume'])
    col_name = find_col(df, KRX_COL_CANDIDATES['name'])
    col_market = find_col(df, KRX_COL_CANDIDATES['market'])
    
    print(f" [KRX-DEBUG] 매칭된 컬럼: code={col_code}, foreign={col_foreign}, inst={col_inst}")
    
    df['code'] = df[col_code].astype(str).str.zfill(6)
    df['foreign_net'] = pd.to_numeric(df[col_foreign], errors='coerce').fillna(0) * 1000
    df['inst_net'] = pd.to_numeric(df[col_inst], errors='coerce').fillna(0) * 1000 
    df['close'] = pd.to_numeric(df[col_close], errors='coerce')
    df['volume'] = pd.to_numeric(df[col_volume], errors='coerce')
    df['name'] = df[col_name]
    df['market'] = df[col_market]
    
    return df[['code','name','market','close','volume','foreign_net','inst_net']]

def fetch_sample(date_str):
    print(" 샘플 데이터 생성")
    return pd.DataFrame({
        'code': ['005930','000660','035420'],
        'name': ['삼성전자','SK하이닉스','NAVER'], 
        'market': ['KOSPI','KOSPI','KOSDAQ'],
        'close': [75000.0, 180000.0, 220000.0],
        'volume': [10000000.0, 3000000.0, 1500000.0],
        'foreign_net': [500000.0, -200000.0, 100000.0],
        'inst_net': [-300000.0, 400000.0, -50000000.0]
    })

def fetch_fdr_universe():
    date_str = datetime.today().strftime("%Y-%m-%d")
    print(f"[DATA] {date_str} 데이터 수집 시작...")
    
    try:
        df = fetch_krx_otp(date_str)
        print(f"[KRX] {len(df)}종목 로드 완료")
        source = "krx-otp"
    except Exception as e:
        print(f" KRX 실패: {e}")
        df = fetch_sample(date_str)
        source = "sample"
    
    if df.empty:
        print(" 데이터 0건. 샘플 데이터로 대체")
        df = fetch_sample(date_str)
        source = "sample"
    
    if DART_API_KEY and source == "krx-otp":
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
