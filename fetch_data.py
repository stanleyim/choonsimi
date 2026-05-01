from pykrx import stock
import pandas as pd
import json
from datetime import datetime, timedelta

def build_universe_bulk():
    print("[UNIVERSE BUILD START - PYKRX BULK]")
    
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    
    # 🔥 핵심: "전체" 지정 시 한 번의 HTTP 요청으로 전체 시장 데이터 반환
    df = stock.get_market_ohlcv_by_date(start, end, "전체")
    df_latest = df.xs(end, level="date")  # 최신일만 필터링
    
    # 종목명 매핑 (필요시 fdr.StockListing과 join)
    kospi = fdr.StockListing("KOSPI")[["Code", "Name"]].set_index("Code")
    kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]].set_index("Code")
    name_df = pd.concat([kospi, kosdaq])
    
    universe = []
    for code, row in df_latest.iterrows():
        name = name_df.loc[code, "Name"] if code in name_df.index else ""
        universe.append({
            "code": code,
            "name": name,
            "close": float(row["종가"]),
            "volume": float(row["거래량"]),
            "foreign_net": 0, "inst_net": 0, "dart_score": 0
        })
        
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({"all": universe, "generated_at": datetime.now().isoformat()}, f, indent=2, ensure_ascii=False)
    print(f"[DONE] universe size = {len(universe)}")

if __name__ == "__main__":
    build_universe_bulk()
