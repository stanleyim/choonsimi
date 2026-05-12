# judge.py — v6.x FINAL (engine.py compatible + production evaluation engine)

import pandas as pd
import numpy as np
from datetime import datetime
import FinanceDataReader as fdr
import pytz

KST = pytz.timezone("Asia/Seoul")


class JudgeEngine:

    def __init__(self):
        self.today = datetime.now(KST).strftime("%Y-%m-%d")

    # --------------------------------------------------
    # 1. LOAD SIGNAL HISTORY (engine output)
    # --------------------------------------------------
    def load_signals(self):
        try:
            df = pd.read_csv("price_history.csv", encoding="utf-8-sig", dtype={"code": str})
            df["code"] = df["code"].str.zfill(6)
            df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            return df
        except Exception as e:
            print(f"[LOAD ERROR] {e}")
            return pd.DataFrame()

    # --------------------------------------------------
    # 2. FETCH CURRENT PRICE (7-day evaluation basis)
    # --------------------------------------------------
    def fetch_exit_price(self, code, entry_date):

        try:
            df = fdr.DataReader(code, entry_date, self.today)

            if df is None or df.empty:
                return None

            return float(df["Close"].iloc[-1])

        except Exception:
            return None

    # --------------------------------------------------
    # 3. PnL CALCULATION
    # --------------------------------------------------
    def compute_return(self, entry_price, exit_price):

        if entry_price <= 0 or exit_price is None:
            return None

        return round((exit_price - entry_price) / entry_price * 100, 2)

    # --------------------------------------------------
    # 4. CORE JUDGE LOGIC
    # --------------------------------------------------
    def evaluate(self, df):

        results = []

        skipped = 0

        for _, row in df.iterrows():

            code = row["code"]
            entry_price = row["entry_price"]
            entry_date = row["entry_date"]

            exit_price = self.fetch_exit_price(code, entry_date)

            if exit_price is None:
                skipped += 1
                continue

            pnl = self.compute_return(entry_price, exit_price)

            if pnl is None:
                skipped += 1
                continue

            results.append({
                "code": code,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl,
                "change_pct_signal": row.get("change_pct", 0)
            })

        return pd.DataFrame(results), skipped

    # --------------------------------------------------
    # 5. PERFORMANCE METRICS (institution-grade)
    # --------------------------------------------------
    def metrics(self, df):

        if df.empty:
            return {}

        pnl = df["pnl_pct"]

        win_rate = (pnl > 0).mean() * 100
        avg_return = pnl.mean()
        max_gain = pnl.max()
        max_loss = pnl.min()

        # Sharpe-like proxy (no risk-free)
        sharpe = 0
        if pnl.std() != 0:
            sharpe = pnl.mean() / pnl.std()

        return {
            "total_trades": len(df),
            "win_rate": round(win_rate, 2),
            "avg_return": round(avg_return, 2),
            "max_gain": round(max_gain, 2),
            "max_loss": round(max_loss, 2),
            "sharpe_proxy": round(sharpe, 3)
        }

    # --------------------------------------------------
    # 6. SAVE OUTPUTS
    # --------------------------------------------------
    def save(self, df, metrics, skipped):

        df.to_csv("signal_history_with_return.csv", index=False, encoding="utf-8-sig")

        summary = {
            "date": self.today,
            "metrics": metrics,
            "skipped": skipped
        }

        import json
        with open("judge_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    # --------------------------------------------------
    # 7. MAIN PIPELINE
    # --------------------------------------------------
    def run(self):

        signals = self.load_signals()

        if signals.empty:
            print("[NO SIGNAL DATA]")
            return

        result_df, skipped = self.evaluate(signals)

        metrics = self.metrics(result_df)

        self.save(result_df, metrics, skipped)

        print("[JUDGE COMPLETE]")
        print(metrics)
        print(f"skipped: {skipped}")


# --------------------------------------------------
# EXEC
# --------------------------------------------------
if __name__ == "__main__":
    judge = JudgeEngine()
    judge.run()
