"""
RegimeEngine v5.5 — Top 20 Stock Recommender (Option A Patch)
────────────────────────────────────────────────────────────
v5.5 패치 내역 (Option A):
  [1] 5-팩터 가중치 적용 (Flow 30%, Mom 25%, Vol 15%, Fund 15%, News 15%)
  [2] 모든 파일 I/O 를 utf-8-sig 로 통일 (모바일 한글/JSON 깨짐 방지)
  [3] news_scores.json 로드 로직 추가 & 결측치 방어 처리
  [4] pd.read_csv 날짜 필터링 버그 수정 (or 연산자 → isin/empty 체크)
  [5] build_reason 에 뉴스 감성 요약 추가
환경: Python 3.11+, pandas, numpy, math, json
────────────────────────────────────────────────────────────
"""

import json, math, os, pandas as pd
from datetime import datetime, timezone, timedelta

REGIME_CACHE   = "regime_cache.json"
SIGNAL_HISTORY = "signal_history.csv"
EMA_ALPHA      = 0.4
TOP_N          = 20
KST            = timezone(timedelta(hours=9))

# ── 5-팩터 가중치 (합계 1.0) ──
W_FLOW        = 0.30
W_MOMENTUM    = 0.25
W_VOLUME      = 0.15
W_FUNDAMENTAL = 0.15
W_NEWS        = 0.15

ETF_KEYWORDS = ["KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
                "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN","FOCUS","RISE"]

ROE_REF, DEBT_REF, GROWTH_REF = 15.0, 150.0, 10.0
ROE_CLIP, DEBT_CLIP, GROWTH_CLIP = (-50.0, 50.0), (0.0, 500.0), (-100.0, 100.0)


# ─────────────────────────── 유틸 ───────────────────────────

def safe_float(v, default=0.0) -> float:
    try: return float(v) if v is not None else default
    except: return default

def ema(values: list) -> float:
    if not values: return 0.0
    e = float(values[0])
    for v in values[1:]: e = EMA_ALPHA * float(v) + (1 - EMA_ALPHA) * e
    return e

def tanh_norm(v: float) -> float:
    """(-inf, inf) → (0.0, 1.0) 정규화"""    return (math.tanh(v) + 1.0) / 2.0

def zscore_norm(val: float, mean: float, std: float) -> float:
    return tanh_norm((val - mean) / std) if std > 0 else 0.5

def calc_fundamental_score(roe: float, debt_ratio: float, op_growth: float) -> float:
    roe    = max(*ROE_CLIP, min(*ROE_CLIP, roe))
    debt   = max(*DEBT_CLIP, min(*DEBT_CLIP, debt_ratio))
    growth = max(*GROWTH_CLIP, min(*GROWTH_CLIP, op_growth))
    return round(
        tanh_norm(roe / ROE_REF) * 0.40 +
        tanh_norm(-debt / DEBT_REF + 1) * 0.35 +
        tanh_norm(growth / GROWTH_REF) * 0.25, 4
    )


# ──────────────────────── StockScorer ────────────────────────

class StockScorer:
    def __init__(self, stocks: list, flow_data: dict, regime: str, fund_map: dict, news_map: dict):
        self.stocks   = stocks
        self.flow     = flow_data
        self.regime   = regime
        self.fund_map = fund_map
        self.news_map = news_map
        self.flow_map = self._build_flow_map()

        vols = [safe_float(s.get("volume")) for s in stocks if safe_float(s.get("volume")) > 0]
        self.vol_mean = sum(vols) / len(vols) if vols else 1.0
        self.vol_std  = (sum((v - self.vol_mean)**2 for v in vols) / len(vols))**0.5 if vols else 1.0

        chgs = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chgs) / len(chgs) if chgs else 0.0
        self.chg_std  = (sum((c - self.chg_mean)**2 for c in chgs) / len(chgs))**0.5 if chgs else 1.0

    def _build_flow_map(self) -> dict:
        fm = {}
        for seg, w in [("KOSPI_foreign", 0.36), ("KOSPI_institution", 0.24),
                       ("KOSDAQ_foreign", 0.24), ("KOSDAQ_institution", 0.16)]:
            for row in self.flow.get(seg, {}).get("rows", []):
                code = str(row.get("code", "")).zfill(6)
                fm[code] = fm.get(code, 0.0) + safe_float(row.get("net")) * w
        return fm

    def score(self, stock: dict) -> float:
        code = str(stock.get("code", "")).zfill(6)
        chg  = safe_float(stock.get("change_rate"))
        vol  = safe_float(stock.get("volume"))
        fd   = self.fund_map.get(code)
        news = self.news_map.get(code, 0.0)
        raw = (
            tanh_norm((self.flow_map.get(code, 0.0) / max(abs(v) for v in self.flow_map.values() or [1.0])) * 3) * W_FLOW +
            zscore_norm(chg, self.chg_mean, self.chg_std) * W_MOMENTUM +
            zscore_norm(vol, self.vol_mean, self.vol_std) * W_VOLUME +
            (calc_fundamental_score(safe_float(fd.get("roe")), safe_float(fd.get("debt_ratio")), safe_float(fd.get("op_growth"))) if fd else 0.5) * W_FUNDAMENTAL +
            tanh_norm(news) * W_NEWS
        ) * ({"UPTREND": 1.05, "DOWNTREND": 0.95}.get(self.regime, 1.0)) * 100

        return round(max(0.0, min(100.0, raw)), 2)

    def build_reason(self, stock: dict) -> str:
        code = str(stock.get("code", "")).zfill(6)
        net  = self.flow_map.get(code, 0.0)
        chg  = safe_float(stock.get("change_rate"))
        fd   = self.fund_map.get(code)
        news = self.news_map.get(code, 0.0)

        flow_txt = f"순매수(net {net:.0f})" if net > 0 else f"순매도(net {net:.0f})" if net < 0 else "수급중립"
        mom_txt  = f"상승({chg:+.1f}%)" if chg > 0 else f"하락({chg:+.1f}%)" if chg < 0 else "보합"
        fund_txt = f"ROE {safe_float(fd.get('roe')):.1f}% / 부채 {safe_float(fd.get('debt_ratio')):.0f}%" if fd else "펀더멘털N/A"
        news_txt = "뉴스호재" if news > 0.3 else "뉴스악재" if news < -0.3 else "뉴스중립"
        return f"{flow_txt} / {mom_txt} / {fund_txt} / {news_txt}"

    def is_etf(self, name) -> bool:
        return any(kw in str(name).upper() for kw in ETF_KEYWORDS)
    def is_preferred(self, name) -> bool:
        n = str(name)
        return n.endswith("우") or "우B" in n or "우C" in n

    def top_n(self) -> list:
        scored = []
        for stock in self.stocks:
            code = str(stock.get("code", "")).zfill(6)
            name = str(stock.get("name", "")).strip()
            if not code or not name or name.lower() == "nan" or self.is_etf(name) or self.is_preferred(name):
                continue
            scored.append((self.score(stock), stock))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = []
        for rank, (s, stock) in enumerate(scored[:TOP_N], 1):
            code = str(stock.get("code", "")).zfill(6)
            fd   = self.fund_map.get(code, {})
            result.append({
                "rank": rank, "code": code, "name": stock.get("name", ""), "score": s,
                "price": int(safe_float(stock.get("close"))),
                "change_pct": round(safe_float(stock.get("change_rate")), 2),
                "volume": int(safe_float(stock.get("volume"))),
                "flow_net": round(self.flow_map.get(code, 0.0), 2),                "roe": safe_float(fd.get("roe")) if fd else None,
                "debt_ratio": safe_float(fd.get("debt_ratio")) if fd else None,
                "op_growth": safe_float(fd.get("op_growth")) if fd else None,
                "reason": self.build_reason(stock)
            })
        return result


# ──────────────────────── RegimeEngine ──────────────────────

class RegimeEngine:
    def __init__(self):
        self.prev_score = 0.0; self.lock_count = 0; self.lock_candidate = "SIDEWAY"; self.last_regime = "SIDEWAY"
        self._load_cache()

    def _load_cache(self):
        try:
            with open(REGIME_CACHE, "r", encoding="utf-8-sig") as f: c = json.load(f)
            self.prev_score = float(c.get("prev_score", 0.0)); self.lock_count = int(c.get("lock_count", 0))
            self.lock_candidate = c.get("lock_candidate", "SIDEWAY"); self.last_regime = c.get("last_regime", "SIDEWAY")
            print(f"[ENGINE] cache loaded → prev={self.prev_score:.3f}")
        except: print("[ENGINE] cache init")

    def _save_cache(self, score: float, regime: str, confidence: float):
        with open(REGIME_CACHE, "w", encoding="utf-8-sig") as f:
            json.dump({"prev_score": score, "lock_count": self.lock_count, "lock_candidate": self.lock_candidate,
                       "last_regime": regime, "last_confidence": confidence, "updated_at": datetime.now(KST).isoformat()}, f, indent=2, ensure_ascii=False)

    def load_market_flow(self) -> dict:
        try:
            with open("market_flow.json", "r", encoding="utf-8-sig") as f: return json.load(f)
        except: print("[WARN] market_flow.json 로드 실패"); return {}

    def load_news_scores(self) -> dict:
        try:
            with open("news_scores.json", "r", encoding="utf-8-sig") as f: return json.load(f).get("scores", {})
        except: print("[WARN] news_scores.json 로드 실패 → 중립 처리"); return {}

    def load_stock_data(self) -> list:
        try:
            df = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            df["code"] = df["code"].astype(str).str.zfill(6)
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            if "date" in df.columns:
                df_today = df[df["date"] == today_str]
                if df_today.empty: df_today = df
            else: df_today = df

            for col in ("close", "volume", "change_rate"):
                if col not in df_today.columns: df_today[col] = 0.0            df_today["change_rate"] = pd.to_numeric(df_today["change_rate"], errors="coerce").fillna(0.0)
            df_today["volume"] = pd.to_numeric(df_today["volume"], errors="coerce").fillna(0)
            print(f"[DATA] {len(df_today)}종목 로드 ({today_str})")
            return df_today.to_dict("records")
        except FileNotFoundError: print("[WARN] history.csv 없음"); return []
        except Exception as e: print(f"[WARN] history.csv 로드 실패: {e}"); return []

    def load_fundamental(self) -> dict:
        try:
            with open("fundamental.json", "r", encoding="utf-8-sig") as f: raw = json.load(f)
            fm = {str(s.get("code", "")).zfill(6): {
                "roe": safe_float(s.get("roe")), "debt_ratio": safe_float(s.get("debt_ratio")),
                "op_growth": safe_float(s.get("op_growth")), "net_income": safe_float(s.get("net_income"))
            } for s in raw.get("stocks", [])}
            print(f"[FUND] {len(fm)}종목 펀더멘털 로드"); return fm
        except: print("[WARN] fundamental.json 없음 → 중립 처리"); return {}

    def compute_flow_score(self, flow: dict) -> float:
        history = flow.get("history", [])
        if history:
            kp_f = ema([h["scores"].get("KOSPI_foreign", 0) for h in history])
            kp_i = ema([h["scores"].get("KOSPI_institution", 0) for h in history])
            kq_f = ema([h["scores"].get("KOSDAQ_foreign", 0) for h in history])
            kq_i = ema([h["scores"].get("KOSDAQ_institution", 0) for h in history])
        else:
            s = flow.get("scores", {})
            kp_f, kp_i, kq_f, kq_i = s.get("KOSPI_foreign",0), s.get("KOSPI_institution",0), s.get("KOSDAQ_foreign",0), s.get("KOSDAQ_institution",0)
        return float(kp_f * 0.36 + kp_i * 0.24 + kq_f * 0.24 + kq_i * 0.16)

    def compute_regime(self, flow: float) -> dict:
        today = flow; final = 0.7 * today + 0.3 * self.prev_score; vol = abs(today - self.prev_score)
        final = max(-1.0, min(1.0, final + vol * 0.2))
        print(f"[ENGINE] today={today:.3f} prev={self.prev_score:.3f} vol={vol:.3f} final={final:.3f}")

        candidate = "UPTREND" if final > 0.5 else "DOWNTREND" if final < -0.5 else "SIDEWAY"
        self.lock_count = self.lock_count + 1 if candidate == self.lock_candidate else 1
        self.lock_candidate = candidate
        lock_thresh = 2 if abs(final) > 0.7 else 3
        regime = candidate if self.lock_count >= lock_thresh else self.last_regime

        base = min(0.95, 0.5 + abs(final) * 0.6); penalty = min(0.15, vol * 0.3)
        confidence = round(max(0.50, base - penalty), 3)
        self.prev_score = final; self.last_regime = regime
        return {"regime": regime, "confidence": confidence, "score": round(final, 4), "volatility": round(vol, 4)}

    def save_result(self, res: dict, flow: dict, top_n: list):
        fs = lambda k: flow.get(k, {}).get("score", 0)
        output = {
            "date": datetime.now(KST).strftime("%Y-%m-%d"), "regime": res["regime"], "confidence": res["confidence"],
            "score": res["score"], "volatility": res["volatility"],            "weights": {"flow": W_FLOW, "momentum": W_MOMENTUM, "volume": W_VOLUME, "fundamental": W_FUNDAMENTAL, "news": W_NEWS},
            "flow_summary": {"KOSPI_foreign": fs("KOSPI_foreign"), "KOSPI_institution": fs("KOSPI_institution"),
                             "KOSDAQ_foreign": fs("KOSDAQ_foreign"), "KOSDAQ_institution": fs("KOSDAQ_institution")},
            "top20": top_n, "updated_at": datetime.now(KST).isoformat()
        }
        with open("result.json", "w", encoding="utf-8-sig") as f: json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[DONE] {res['regime']} / confidence={res['confidence']}")
        for item in top_n: print(f"  {item['rank']:>2}. {item['code']} {item['name']:<12} score={item['score']:.1f} | {item['reason']}")

    def save_signal_history(self, top_n: list, regime: str):
        today = datetime.now(KST).strftime("%Y-%m-%d")
        rows = [{"date": today, "regime": regime, "rank": i["rank"], "code": i["code"], "name": i["name"],
                 "score": i["score"], "price": i["price"], "change_pct": i["change_pct"], "flow_net": i["flow_net"],
                 "roe": i.get("roe",""), "debt_ratio": i.get("debt_ratio",""), "op_growth": i.get("op_growth","")} for i in top_n]
        df_new = pd.DataFrame(rows)
        try:
            df_old = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")
            df_old = df_old[df_old["date"] != today]
            df_out = pd.concat([df_old, df_new], ignore_index=True)
        except: df_out = df_new
        df_out.to_csv(SIGNAL_HISTORY, index=False, encoding="utf-8-sig")
        print(f"[SIGNAL] {len(rows)}개 → {SIGNAL_HISTORY} 저장 (누적 {len(df_out)}행)")

    def run(self):
        flow     = self.load_market_flow()
        news_map = self.load_news_scores()
        stocks   = self.load_stock_data()
        fund_map = self.load_fundamental()

        res = self.compute_regime(self.compute_flow_score(flow))
        top_n = StockScorer(stocks, flow, res["regime"], fund_map, news_map).top_n() if stocks else []
        self.save_result(res, flow, top_n)
        self.save_signal_history(top_n, res["regime"])
        self._save_cache(res["score"], res["regime"], res["confidence"])


if __name__ == "__main__":
    RegimeEngine().run()
