import FinanceDataReader as fdr
import pandas as pd
import json
from datetime import datetime

def build_universe():
    print("[UNIVERSE BUILD START]")

    # KOSPI + KOSDAQ 전체
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")

    df = pd.concat([kospi, kosdaq], ignore_index=True)

    universe = []

    for _, row in df.iterrows():
        code = str(row["Code"]).zfill(6)

        try:
            price = fdr.DataReader(code).tail(1)
            if price.empty:
                continue

            universe.append({
                "code": code,
                "name": row.get("Name", ""),
                "close": float(price["Close"].values[-1]),
                "volume": float(price["Volume"].values[-1]),
                "foreign_net": 0,
                "inst_net": 0,
                "dart_score": 0
            })

        except:
            continue

    output = {
        "all": universe,
        "generated_at": datetime.now().isoformat()
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[DONE] universe size = {len(universe)}")


if __name__ == "__main__":
    build_universe()
