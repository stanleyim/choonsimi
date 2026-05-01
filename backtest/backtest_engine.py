import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# =========================
# CONFIG
# =========================
TOP_N = 10

# =========================
# UNIVERSE (KRX → Yahoo 변환)
# =========================
def get_universe():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        codes = [x["code"] for x in data.get("all", [])[:TOP_N]]

        # KRX → Yahoo Finance (기본: KOSPI)
        # 필요 시 KQ 분리 로직 확장 가능
        return [c + ".KS" for c in codes]

    except Exception as e:
        print("[ERROR] data.json load failed:", e)
        return []


# =========================
# PRICE LOADER (SAFE)
# =========================
def load_price(ticker):
    try:
        df = yf.download(ticker, period="5y", progress=False)

        if df is None or df.empty or "Close" not in df.columns:
            return None

        df = df[["Close"]].dropna()
        if len(df) < 100:
            return None

        return df

    except Exception:
        return None


# =========================
# RETURNS
# =========================
def calc_returns(df):
    df = df.copy()
    df["ret"] = df["Close"].pct_change()
    df = df.dropna()

    df["cum"] = (1 + df["ret"]).cumprod()
    return df


# =========================
# METRICS
# =========================
def compute_metrics(df):
    if df is None or len(df) < 50:
        return None

    total_return = df["cum"].iloc[-1] - 1

    peak = df["cum"].cummax()
    dd = (df["cum"] / peak) - 1
    mdd = dd.min()

    volatility = df["ret"].std()

    # Annualized Sharpe (proxy)
    sharpe = 0
    if volatility > 0:
        sharpe = (df["ret"].mean() * 252) / (volatility * np.sqrt(252) + 1e-9)

    return {
        "return": round(total_return * 100, 2),
        "mdd": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3)
    }


# =========================
# BACKTEST CORE
# =========================
def backtest(universe):
    results = {}

    for t in universe:
        df = load_price(t)
        if df is None:
            continue

        df = calc_returns(df)
        metrics = compute_metrics(df)

        if metrics is None:
            continue

        results[t] = metrics

    return results


# =========================
# PORTFOLIO SUMMARY
# =========================
def portfolio_summary(results):
    if not results:
        return None

    rets = [v["return"] for v in results.values()]
    mdds = [v["mdd"] for v in results.values()]
    sharpe = [v["sharpe"] for v in results.values()]

    return {
        "avg_return": round(np.mean(rets), 2),
        "avg_mdd": round(np.mean(mdds), 2),
        "avg_sharpe": round(np.mean(sharpe), 3),
        "best_return": max(rets),
        "worst_return": min(rets)
    }


# =========================
# MAIN
# =========================
def main():
    print("[BACKTEST START]")

    universe = get_universe()

    if not universe:
        print("[SKIP] empty universe")
        return

    results = backtest(universe)
    summary = portfolio_summary(results)

    print("\n===== INDIVIDUAL RESULTS =====")
    for k, v in results.items():
        print(k, v)

    print("\n===== PORTFOLIO SUMMARY =====")
    print(summary)

    output = {
        "date": datetime.now().isoformat(),
        "results": results,
        "summary": summary
    }

    pd.DataFrame.from_dict(results, orient="index").to_csv(
        "backtest_result.csv"
    )

    with open("backtest_result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n[DONE] saved backtest_result")


if __name__ == "__main__":
    main()
