# fetch_data.py
from pykrx import stock
import FinanceDataReader as fdr  # 🔥 1. 임포트 추가
import pandas as pd
import json
from datetime import datetime, timedelta
import time

def build_universe_bulk():
    print("[UNIVERSE BUILD START - PYKRX BULK]")
    
    today = datetime.now()
    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=7)).strftime("%Y%m%d")
    
    try:
        # 🔥 2. 정확한 pykrx 함수 사용: market="KOSPI+KOSDAQ" 또는 "전체"
        # 참고: pykrx 버전에 따라 "전체" 대신 "KOSPI+KOSDAQ" 사용 권장
        df = stock.get_market_ohlcv_by_date(start, end, market="전체")
        df_latest = df.xs(end, level="date").copy()
        
    except Exception as e:
        print(f"[WARN] pykrx bulk fetch failed: {e}")
        print("[INFO] Falling back to FinanceDataReader...")
        return build_universe_fallback()
    
    # 종목명 매핑 (FinanceDataReader 활용)
    try:
        kospi = fdr.StockListing("KOSPI")[["Code", "Name"]].set_index("Code")
        kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]].set_index("Code")
        name_df = pd.concat([kospi, kosdaq])
    except Exception as e:
        print(f"[WARN] Failed to load stock names: {e}")
        name_df = pd.DataFrame(columns=["Name"])
    
    universe = []
    for code, row in df_latest.iterrows():
        code_str = str(code).zfill(6)
        # 🔥 3. 안전한 인덱스 접근
        name = str(name_df.loc[code_str, "Name"]) if code_str in name_df.index else ""
        
        universe.append({
            "code": code_str,
            "name": name,
            "close": float(row["종가"]),
            "volume": float(row["거래량"]),
            "foreign_net": 0,
            "inst_net": 0,
            "dart_score": 0
        })
    
    output = {"all": universe, "generated_at": datetime.now().isoformat()}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"[DONE] universe size = {len(universe)}")
    return universe

def build_universe_fallback():
    """pykrx 실패 시 사용하는 폴백 로직 (병렬 + 기간 제한)"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")
    df = pd.concat([kospi, kosdaq], ignore_index=True)
    
    def fetch_one(code, name):
        try:
            code_z = str(code).zfill(6)
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
            price = fdr.DataReader(code_z, start=start, end=end)
            if price.empty: return None
            latest = price.iloc[-1]
            return {
                "code": code_z, "name": str(name),
                "close": float(latest["Close"]), "volume": float(latest["Volume"]),
                "foreign_net": 0, "inst_net": 0, "dart_score": 0
            }
        except: return None
    
    tasks = [(str(row["Code"]), row.get("Name", "")) for _, row in df.iterrows()]
    universe = []
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_one, c, n): c for c, n in tasks}
        for fut in as_completed(futures):
            res = fut.result()
            if res: universe.append(res)
            time.sleep(0.01)  # 레이트 리밋 방지
    
    return universe

if __name__ == "__main__":
    build_universe_bulk()
