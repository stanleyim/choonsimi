"""
RegimeEngine v6.2.5 FINAL — Fully Stable Production Build
────────────────────────────────────────────────────
✔ Syntax Error FIXED
✔ Indentation FIXED
✔ Score Logic FIXED
✔ Fallback Filter FIXED
✔ ZeroDivision SAFE
✔ Universe Tracking Added
────────────────────────────────────────────────────
"""

import json, math, pandas as pd
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

SIGNAL_HISTORY = "signal_history.csv"
PERF_LOG = "performance_log.json"
RESULT_FILE = "result.json"

TOP_N = 20

W_FLOW, W_MOM, W_VOL, W_FUND, W_NEWS = 0.30, 0.25, 0.15, 0.15, 0.15


def safe_float(v, d=0.0):
    try:
        return float(v)
    except:
        return d


def tanh_norm(v):
    return (math.tanh(v) + 1) / 2


def zscore_norm(v, m, s):
    return tanh_norm((v - m) / s) if s > 0 else 0.5


def is_common_stock(name):
    if not name:
        return False

    name = str(name).upper()

    blacklist = [
        "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
        "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
        "LEVERAGE","INVERSE","레버리지","인버스","곱버스",
        "INDEX","지수"
    ]

    return not any(k in name for k in blacklist)


class StockScorer:
    def __init__(self, stocks, flow, regime, fund, news):
        self.stocks = stocks
        self.flow = flow
        self.regime = regime
        self.fund = fund
        self.news = news

        self.flow_map = self._flow_map()
        self.flow_max = max(abs(v) for v in self.flow_map.values()) or 1.0

        vols = [safe_float(s.get("volume")) for s in stocks]
        self.vol_mean = sum(vols)/len(vols) if vols else 1
        self.vol_std = (sum((v-self.vol_mean)**2 for v in vols)/len(vols))**0.5 if vols else 1

        chg = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chg)/len(chg) if chg else 0
        self.chg_std = (sum((c-self.chg_mean)**2 for c in chg)/len(chg))**0.5 if chg else 1

    def _flow_map(self):
        fm = {}

        for seg, w in [
            ("KOSPI_foreign",0.36),
            ("KOSPI_institution",0.24),
            ("KOSDAQ_foreign",0.24),
            ("KOSDAQ_institution",0.16)
        ]:
            for r in self.flow.get(seg, {}).get("rows", []):
                c = str(r.get("code","")).zfill(6)
                fm[c] = fm.get(c, 0) + safe_float(r.get("net")) * w

        return fm

    def score(self, s):
        code = str(s.get("code","")).zfill(6)

        chg = safe_float(s.get("change_rate"))
        vol = safe_float(s.get("volume"))
        fd = self.fund.get(code, {})
        news = self.news.get(code, 0)

        penalty = 1.0
        if chg > 10:
            penalty = max(0.65, 1 - (chg - 10) * 0.035)

        raw = (
            tanh_norm(self.flow_map.get(code, 0) / self.flow_max * 3) * W_FLOW +
            zscore_norm(chg, self.chg_mean, self.chg_std) * W_MOM * penalty +
            zscore_norm(vol, self.vol_mean, self.vol_std) * W_VOL +
            (tanh_norm(safe_float(fd.get("roe")) / 10) if fd else 0.5) * W_FUND +
            tanh_norm(news) * W_NEWS
        )

        regime_factor = (
            1.05 if self.regime == "UPTREND"
            else 0.95 if self.regime == "DOWNTREND"
            else 1.0
        )

        raw *= regime_factor * 100

        return round(max(0, min(100, raw)), 2)

    def top_n(self):
        scored = [(self.score(s), s) for s in self.stocks]
        scored.sort(reverse=True, key=lambda x: x[0])

        return [
            {
                "rank": i,
                "code": str(s.get("code","")).zfill(6),
                "name": s.get("name",""),
                "score": sc,
                "price": int(safe_float(s.get("close"))),
                "change_pct": safe_float(s.get("change_rate"))
            }
            for i, (sc, s) in enumerate(scored[:TOP_N], 1)
        ]


class RegimeEngine:
    def load_json(self, f):
        try:
            with open(f, encoding="utf-8-sig") as fp:
                return json.load(fp)
        except:
            return {}

    def load_stock_data(self):
        try:
            df = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            today = datetime.now(KST).strftime("%Y-%m-%d")

            if "date" not in df.columns:
                return []

            return df[df["date"] == today].to_dict("records")

        except:
            return []

    def compute_regime(self, flow):
        segs = [
            "KOSPI_foreign",
            "KOSPI_institution",
            "KOSDAQ_foreign",
            "KOSDAQ_institution"
        ]

        score = sum(flow.get(s, {}).get("score", 0) for s in segs) / 4

        return {
            "regime": "UPTREND" if score > 0.5 else "DOWNTREND" if score < -0.5 else "SIDEWAY",
            "confidence": round(abs(score), 2)
        }

    def pre_filter(self, stocks, regime):
        FILTER_MAP = {
            "UPTREND": {"min_vol": 30000, "min_price": 1000},
            "SIDEWAY": {"min_vol": 20000, "min_price": 1000},
            "DOWNTREND": {"min_vol": 40000, "min_price": 2000}
        }

        cfg = FILTER_MAP.get(regime, FILTER_MAP["SIDEWAY"])

        filtered = [
            s for s in stocks
            if safe_float(s.get("volume")) >= cfg["min_vol"]
            and safe_float(s.get("close")) >= cfg["min_price"]
            and is_common_stock(s.get("name"))
        ]

        if len(filtered) < 15 and len(stocks) >= 15:
            print(f"[FILTER] relaxing: {len(filtered)} < 15 → fallback")

            r = {
                "min_vol": cfg["min_vol"] * 0.5,
                "min_price": cfg["min_price"] * 0.5
            }

            filtered = [
                s for s in stocks
                if safe_float(s.get("volume")) >= r["min_vol"]
                and safe_float(s.get("close")) >= r["min_price"]
                and is_common_stock(s.get("name"))
            ]

        return filtered

    def get_data_quality(self):
        try:
            df = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            if df.empty:
                return "error"
            return "full" if df["date"].max() == datetime.now(KST).strftime("%Y-%m-%d") else "lagged"
        except:
            return "error"

    def verify(self):
        try:
            hist = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            hist["code"] = hist["code"].str.zfill(6)

            price_map = {k: v for k, v in zip(hist["code"], hist["close"]) if v > 0}

            sig = pd.read_csv(SIGNAL_HISTORY, encoding="utf-8-sig")

            y = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
            sig = sig[sig["date"] == y]

            if sig.empty:
                return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

            hits, total, avg, top5 = 0, len(sig), 0, []

            for _, r in sig.iterrows():
                code = str(r["code"]).zfill(6)
                entry = safe_float(r.get("price"))
                exitp = price_map.get(code, 0)

                if entry > 0 and exitp > 0:
                    ret = (exitp - entry) / entry * 100
                    avg += ret
                    if ret > 0:
                        hits += 1
                    if r.get("rank", 999) <= 5:
                        top5.append(ret)

            return {
                "win_rate": round(hits / total * 100, 1) if total > 0 else 0,
                "avg_return": round(avg / total, 2) if total > 0 else 0,
                "top5_return": round(sum(top5) / len(top5), 2) if top5 else 0
            }

        except:
            return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

    def save_signal_history(self, top, regime):
        today = datetime.now(KST).strftime("%Y-%m-%d")

        df = pd.DataFrame([
            {
                "date": today,
                "regime": regime,
                "rank": t.get("rank"),
                "code": t.get("code"),
                "name": t.get("name"),
                "score": t.get("score"),
                "price": t.get("price"),
                "change_pct": t.get("change_pct")
            }
            for t in top
        ])

        try:
            old = pd.read_csv(SIGNAL_HISTORY, encoding="utf-8-sig")
            old = old[old["date"] != today]
            df = pd.concat([old, df], ignore_index=True)
        except:
            pass

        df.to_csv(SIGNAL_HISTORY, index=False, encoding="utf-8-sig")

    def run(self):
        flow = self.load_json("market_flow.json")
        news = self.load_json("news_scores.json").get("scores", {})
        raw_fund = self.load_json("fundamental.json").get("stocks", [])

        fund = {str(s.get("code", "")).zfill(6): s for s in raw_fund}

        stocks = self.load_stock_data()
        reg = self.compute_regime(flow)

        stocks = self.pre_filter(stocks, reg["regime"])

        print(f"[ENGINE] Universe size: {len(stocks)}")

        scorer = StockScorer(stocks, flow, reg["regime"], fund, news)

        top = scorer.top_n()
        perf = self.verify()

        self.save_signal_history(top, reg["regime"])

        result = {
            "date": datetime.now(KST).strftime("%Y-%m-%d"),
            "regime": reg["regime"],
            "universe_size": len(stocks),
            "confidence": reg["confidence"],
            "data_quality": self.get_data_quality(),
            "top20": top,
            "performance_today": perf
        }

        with open(RESULT_FILE, "w", encoding="utf-8-sig") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print("[DONE]", reg["regime"], "| TOP:", len(top), "| PERF:", perf)


if __name__ == "__main__":
    RegimeEngine().run()
