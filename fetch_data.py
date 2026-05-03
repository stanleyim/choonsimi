"""fetch_data.py — v5.2 FINAL with FDR Supply 20D"""
import io, json, os, shutil
from datetime import datetime, timedelta
import pandas as pd, requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "data.json")
BACKUP_FILE = os.path.join(ROOT, "data.json.bak")
MIN_STOCKS, MAX_DAYS, TIMEOUT, FLOW_DAYS = 50, 20, 30, 20 # 20일로 변경

def trading_dates(max_days=MAX_DAYS):
    dates, cur = [], datetime.today()
    while len(dates) < max_days:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)
    return dates

def to_num(series, default=0.0):
    return series.astype(str).str.replace(",","",regex=False).str.replace("(","-",regex=False).str.replace(")","",regex=False).pipe(pd.to_numeric, errors="coerce").fillna(default)

def add_placeholders(df, date):
    for col in ["foreign_net", "inst_net"]:
        if col not in df.columns:
            df[col] = 0
    df["dart_score"] = 0
    df["date"] = date
    return df

def fetch_fdr_direct_with_supply():
    try:
        import FinanceDataReader as fdr
        date = trading_dates()[0]
        start_date = trading_dates()[MAX_DAYS-1] # 20일 전
        
        tickers = fdr.StockListing('KOSPI')['Code'].tolist() + fdr.StockListing('KOSDAQ')['Code'].tolist()
        tickers = list(set(tickers))[:MIN_STOCKS]
        
        all_data = []
        for t in tickers:
            try:
                df = fdr.DataReader(t, start_date, date)
                if df.empty or len(df) < 5: # MA5 계산하려면 최소 5일치 필요
                    continue
                    
                df = df.reset_index()
                df['ticker'] = t
                df['date'] = df['Date'].dt.strftime('%Y-%m-%d')
                df['foreign_net'] = to_num(df['Foreign'])
                df['inst_net'] = to_num(df['Institution'])
                
                # 최신일 데이터만 추출
                latest = df.iloc[-1:]
                all_data.append(latest[['ticker', 'date', 'Close', 'Volume', 'Open', 'High', 'Low', 'foreign_net', 'inst_net']])
                
            except Exception as e:
                continue
                
        if not all_data:
            return None, None, None
            
        df = pd.concat(all_data, ignore_index=True)
        df.columns = ['code', 'date', 'close', 'volume', 'open', 'high', 'low', 'foreign_net', 'inst_net']
        df['code'] = df['code'].astype(str).str.zfill(6)
        df['name'] = ''
        df['market'] = ''
        
        df = df[(df['close'] > 0) & (df['volume'] > 0)].drop_duplicates('code').reset_index(drop=True)
        
        if len(df) < MIN_STOCKS:
            return None, None, None
            
        print(f" [FDR-DIRECT] 성공: {len(df)}종목, 20일치 수급 포함")
        return add_placeholders(df, date), date, "fdr_direct"
        
    except ImportError:
        print(" [FDR-DIRECT] finance-datareader 미설치")
        return None, None, None
    except Exception as e:
        print(f" [FDR-DIRECT] 실패: {e}")
        return None, None, None

def fetch_placeholder():
    date = datetime.today().strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "code": ["005930","000660","035420"],
        "name": ["삼성전자","SK하이닉스","네이버"],
        "market": ["KOSPI"]*3,
        "close": [0.0]*3,
        "volume": [0]*3,
        "open": [0.0]*3,
        "high": [0.0]*3,
        "low": [0.0]*3,
        "foreign_net": [0.0]*3,
        "inst_net": [0.0]*3
    })
    print(f" [PLACEHOLDER] {len(df)}종목 — 파이프라인 생존 모드")
    return add_placeholders(df, date), date, "placeholder"

def save_data(df, date, source):
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)
    payload = {"date": date, "source": source, "count": len(df), "all": df.to_dict("records")}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f" {DATA_FILE} → {len(df)}종목 (source={source})")

if __name__ == "__main__":
    print("[UNIVERSE BUILD START]")
    df, date, source = fetch_fdr_direct_with_supply()
    if df is None:
        df, date, source = fetch_placeholder()
    save_data(df, date, source)
    print(f"[UNIVERSE BUILD DONE] source={source} count={len(df)}")
