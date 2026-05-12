# engine.py — v6.x FINAL HARDENED (judge-compatible + production stable)

import pandas as pd
import numpy as np
import json
import time
from datetime import datetime
import FinanceDataReader as fdr

# KST 설정 (이미 프로젝트에 있으면 생략 가능)
import pytz
KST = pytz.timezone("Asia/Seoul")


class RegimeEngine:

    def __init__(self):
        self.today = datetime.now(KST).strftime("%Y-%m-%d")

    # --------------------------------------------------
    # 1. DATA LOAD (history.csv → today filter safe)
    # --------------------------------------------------
    def load_stock_data(self):
        try:
            df = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")

            df["code"] = df["code"].str.zfill(6)

            # SAFE DATE NORMALIZATION (critical fix #3)
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

            return df[df["date"] == self.today].to_dict("records")

        except Exception as e:
            print(f"[LOAD ERROR] {e}")
            return []

    # --------------------------------------------------
    # 2. PRICE FETCH (FDR SAFE MODE)
    # --------------------------------------------------
    def fetch_price(self, code):
        try:
            df = fdr.DataReader(code, self.today, self.today)

            if df is None or df.empty:
                return None

            return df.iloc[-1]

        except (ValueError, KeyError, Exception):
            return None

    # --------------------------------------------------
    # 3. SCORE ENGINE (placeholder logic 유지 가능)
    # --------------------------------------------------
    def score_stock(self, row):
        # 기존 전략 유지 영역
        score = (
            (row.get("momentum", 0) * 0.4) +
            (row.get("volume_ratio", 1) * 0.3) +
            (row.get("flow", 0) * 0.3)
        )
        return round(score, 2)

    # --------------------------------------------------
    # 4. TOP SELECTION (entry_top5 핵심)
    # --------------------------------------------------
    def select_top5(self, data):

        scored = []

        for row in data:
            code = row["code"]

            price_data = self.fetch_price(code)
            if price_data is None:
                continue

            try:
                price = float(price_data["Close"])
                prev_cl = float(price_data["Close"])  # same-day fallback

                score = self.score_stock(row)

                scored.append({
                    "code": code,
                    "score": score,
                    "price": price,
                    "prev_close": prev_cl,
                    "change_pct": row.get("change_pct"),  # keep raw signal if exists
                    "entry_date": self.today
                })

            except Exception:
                continue

            time.sleep(0.2)  # FDR rate limit protection

        # sort by score
        scored = sorted(scored, key=lambda x: x["score"], reverse=True)

        return scored[:5]

    # --------------------------------------------------
    # 5. PRICE HISTORY BUILD (judge.py 핵심 데이터)
    # --------------------------------------------------
    def build_price_history(self, top5):

        history = []

        for row in top5:

            code = row["code"]

            price = row["price"]
            prev_cl = row["prev_close"]

            # --------------------------------------------------
            # FIX #1: change_pct SAFE LOGIC (NO score misuse)
            # --------------------------------------------------
            change_pct = row.get("change_pct")

            if change_pct is None or pd.isna(change_pct):
                if prev_cl and prev_cl > 0:
                    change_pct = round((price - prev_cl) / prev_cl * 100, 2)
                else:
                    change_pct = 0.0

            history.append({
                "code": code,
                "entry_price": price,
                "entry_date": self.today,
                "change_pct": change_pct
            })

        # save minimal entry dataset (judge compatible)
        df = pd.DataFrame(history)
        df.to_csv("price_history.csv", index=False, encoding="utf-8-sig")

        return history

    # --------------------------------------------------
    # 6. OUTPUT
    # --------------------------------------------------
    def save_outputs(self, top5):

        # result.json (UI)
        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(top5, f, ensure_ascii=False, indent=2)

        # signal_history.csv (full trace)
        pd.DataFrame(top5).to_csv(
            "signal_history.csv",
            index=False,
            encoding="utf-8-sig"
        )

    # --------------------------------------------------
    # 7. MAIN PIPELINE
    # --------------------------------------------------
    def run(self):

        data = self.load_stock_data()

        if not data:
            print("[NO DATA]")
            return

        top5 = self.select_top5(data)

        self.save_outputs(top5)

        self.build_price_history(top5)

        print(f"[DONE] engine run complete → {self.today}")


# --------------------------------------------------
# EXEC
# --------------------------------------------------
if __name__ == "__main__":
    engine = RegimeEngine()
    engine.run()
