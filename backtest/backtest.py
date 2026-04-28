import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

# ─────────────────────────────
# 전략 입력 (기존 engine 결과)
# ─────────────────────────────
def get_universe():
    # 실제로는 engine output.json에서 가져오면 됨
    # 여기선 예시
    return [
        "005930.KS",  # 삼성전자
        "000660.KS",  # SK하이닉스
        "035420.KS",  # NAVER
        "051910.KS",  # LG화학
        "006400.KS",  # 삼성SDI
    ]

# ─────────────────────────────
# 가격 데이터
# ─────────────────────────────
def load_price(ticker):
    try:
        df = yf.download(ticker, period="5y", progress=False)
        df = df[["Close"]].dropna()
        return df
    except:
        return None

# ─────────────────────────────
# 수익률 계산
# ─────────────────────────────
def calc_return(df):
    df["ret"] = df["Close"].pct_change()
    df["cum"] = (1 + df["ret"]).cumprod()
    return df

# ─────────────────────────────
# 백테스트 (핵심)
# ─────────────────────────────
def backtest(universe):
    results = {}

    for t in universe:
        df = load_price(t)
        if df is None or len(df) < 100:
            continue

        df = calc_return(df)

        total_return = df["cum"].iloc[-1] - 1

        # MDD (최대 낙폭)
        peak = df["cum"].cummax()
        dd = (df["cum"] / peak) - 1
        mdd = dd.min()

        results[t] = {
            "return": round(total_return * 100, 2),
            "mdd": round(mdd * 100, 2)
        }

    return results

# ─────────────────────────────
# 전략 시뮬레이션 (TOP10 평균)
# ─────────────────────────────
def portfolio_sim(results):
    rets = [v["return"] for v in results.values()]
    mdds = [v["mdd"] for v in results.values()]

    if not rets:
        return None

    return {
        "avg_return": round(np.mean(rets), 2),
        "avg_mdd": round(np.mean(mdds), 2),
        "best": max(rets),
        "worst": min(rets)
    }

# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main():
    print("[V1 BACKTEST] START")

    universe = get_universe()
    results = backtest(universe)

    summary = portfolio_sim(results)

    print("\n📊 RESULT")
    print("──────────────")

    for k, v in results.items():
        print(k, v)

    print("\n📈 PORTFOLIO")
    print(summary)

    # 저장
    output = {
        "date": datetime.now().isoformat(),
        "results": results,
        "summary": summary
    }

    pd.DataFrame.from_dict(results, orient="index").to_csv("backtest_result.csv")

    print("\n[DONE] saved backtest_result.csv")

if __name__ == "__main__":
    main()
