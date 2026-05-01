import json, time
from datetime import datetime, timedelta
from pathlib import Path
from pykrx import stock
import pandas as pd

CACHE_DATA = Path("data.json")
TTL_H = 24

def build_universe():
    t0 = time.time()
    print("[UNIVERSE BUILD START]")

    # =========================
    # 1. CACHE FIRST
    # =========================
    if CACHE_DATA.exists():
        age = (time.time() - CACHE_DATA.stat().st_mtime) / 3600
        if age < TTL_H:
            print(f"[CACHE HIT] {age:.1f}h old")
            return

    # =========================
    # 2. FIND VALID TRADING DAY
    # =========================
    latest_df = None

    for i in range(5):
        target = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")

        try:
            df = stock.get_market_ohlcv_by_date(target, target, "전체")
            if df is not None and not df.empty:
                print(f"[OK] trading day = {target}")
                latest_df = df
                break
        except Exception:
            continue

    if latest_df is None:
        print("[ERROR] no market data found")
        return

    # =========================
    # 3. SAFE NORMALIZATION (ROBUST)
    # =========================
    df = latest_df.copy()
    df = df.reset_index()

    # pykrx 안정 구조:
    # index reset 후 → [날짜, 티커, 시가, 고가, 저가, 종가, 거래량]

    # 컬럼 자동 감지 (CI-safe)
    col_map = {}

    for c in df.columns:
        if "티커" in c or "ticker" in c.lower():
            col_map[c] = "code"
        elif "종가" in c or "close" in c.lower():
            col_map[c] = "close"
        elif "거래량" in c or "volume" in c.lower():
            col_map[c] = "volume"

    df = df.rename(columns=col_map)

    # code fallback (index 기반)
    if "code" not in df.columns:
        df["code"] = df.iloc[:, 1]  # 안전 fallback

    if "close" not in df.columns:
        df["close"] = df.iloc[:, -2]

    if "volume" not in df.columns:
        df["volume"] = df.iloc[:, -1]

    # =========================
    # 4. BUILD UNIVERSE (FAST)
    # =========================
    universe = (
        df[["code", "close", "volume"]]
        .dropna()
        .assign(
            code=lambda x: x["code"].astype(str).str.zfill(6),
            foreign_net=0,
            inst_net=0,
            dart_score=0
        )
        .to_dict("records")
    )

    # =========================
    # 5. SAVE
    # =========================
    out = {
        "all": universe,
        "generated_at": datetime.now().isoformat()
    }

    CACHE_DATA.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"[DONE] {len(universe)} stocks | {time.time()-t0:.2f}s")


if __name__ == "__main__":
    build_universe()
