"""
RegimeEngine v5.3 — Top 10 Stock Recommender
────────────────────────────────────────────────────────────
data.json 구조 확정 적용:
  - 최상위 키: "all" (list of records)
  - 필드: code, name, close, volume, change_rate
  - high/low 없음 → 안정성 팩터 제거

3-팩터 채점:
  수급 (외국인+기관 net)   40%
  가격 모멘텀 (change_rate) 35%
  거래량 강도 (volume)     25%
────────────────────────────────────────────────────────────
"""

import json
import math
from datetime import datetime

REGIME_CACHE = "regime_cache.json"
EMA_ALPHA    = 0.4

W_FLOW     = 0.40
W_MOMENTUM = 0.35
W_VOLUME   = 0.25

ETF_KEYWORDS = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF",
    "HANARO","TIMEFOLIO","TREX","SOL","ACE","ETF","ETN","FOCUS","RISE"
]


def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def ema(values: list) -> float:
    if not values:
        return 0.0
    e = float(values[0])
    for v in values[1:]:
        e = EMA_ALPHA * float(v) + (1 - EMA_ALPHA) * e
    return e


def tanh_norm(v: float) -> float:
    return (math.tanh(v) + 1) / 2


def zscore_norm(val, mean, std) -> float:
    if std <= 0:
        return 0.5
    return tanh_norm((val - mean) / std)


def dynamic_confidence(score: float, volatility: float) -> float:
    base    = min(0.95, 0.5 + abs(score) * 0.6)
    penalty = min(0.15, volatility * 0.3)
    return round(max(0.50, base - penalty), 3)


def dynamic_lock_threshold(score: float) -> int:
    return 2 if abs(score) > 0.7 else 3


class StockScorer:

    def __init__(self, stocks: list, flow_data: dict, regime: str):
        self.stocks = stocks
        self.flow   = flow_data
        self.regime = regime
        self.flow_map = self._build_flow_map()

        vols = [safe_float(s.get("volume")) for s in stocks if safe_float(s.get("volume")) > 0]
        self.vol_mean = sum(vols) / len(vols) if vols else 1.0
        self.vol_std  = (sum((v - self.vol_mean)**2 for v in vols) / len(vols))**0.5 if vols else 1.0

        chgs = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chgs) / len(chgs) if chgs else 0.0
        self.chg_std  = (sum((c - self.chg_mean)**2 for c in chgs) / len(chgs))**0.5 if chgs else 1.0

    def _build_flow_map(self) -> dict:
        fm = {}
        segments = [
            ("KOSPI_foreign",      0.36),
            ("KOSPI_institution",  0.24),
            ("KOSDAQ_foreign",     0.24),
            ("KOSDAQ_institution", 0.16),
        ]
        for seg, w in segments:
            rows = self.flow.get(seg, {}).get("rows", [])
            for row in rows:
                code = str(row.get("code", "")).zfill(6)
                net  = safe_float(row.get("net"))
                fm[code] = fm.get(code, 0.0) + net * w
        return fm

    def _flow_score(self, code: str) -> float:
        net = self.flow_map.get(code, 0.0)
        if not self.flow_map:
            return 0.5
        max_net = max(abs(v) for v in self.flow_map.values()) or 1.0
        return tanh_norm((net / max_net) * 3)

    def _momentum_score(self, chg: float) -> float:
        return zscore_norm(chg, self.chg_mean, self.chg_std)

    def _volume_score(self, vol: float) -> float:
        return zscore_norm(vol, self.vol_mean, self.vol_std)

    def _regime_multiplier(self) -> float:
        return {"UPTREND": 1.05, "DOWNTREND": 0.95}.get(self.regime, 1.0)

    def score(self, stock: dict) -> float:
        code = str(stock.get("code", "")).zfill(6)
        chg  = safe_float(stock.get("change_rate"))
        vol  = safe_float(stock.get("volume"))
        total = (self._flow_score(code)    * W_FLOW +
                 self._momentum_score(chg) * W_MOMENTUM +
                 self._volume_score(vol)   * W_VOLUME)
        return round(total * self._regime_multiplier() * 100, 2)

    def build_reason(self, stock: dict) -> str:
        code = str(stock.get("code", "")).zfill(6)
        net  = self.flow_map.get(code, 0.0)
        chg  = safe_float(stock.get("change_rate"))

        if net >= 10:
            flow_txt = f"외국인·기관 강한 순매수 (net {net:.0f})"
        elif net > 0:
            flow_txt = f"외국인·기관 순매수 (net {net:.0f})"
        elif net == 0:
            flow_txt = "수급 중립"
        else:
            flow_txt = f"외국인·기관 순매도 (net {net:.0f})"

        if chg >= 3:
            mom_txt = f"강한 상승 모멘텀 (+{chg:.1f}%)"
        elif chg > 0:
            mom_txt = f"소폭 상승 (+{chg:.1f}%)"
        elif chg == 0:
            mom_txt = "보합"
        elif chg > -3:
            mom_txt = f"소폭 하락 ({chg:.1f}%)"
        else:
            mom_txt = f"하락 주의 ({chg:.1f}%)"

        return f"{flow_txt} / {mom_txt}"

    def is_etf(self, name: str) -> bool:
        return any(kw in name.upper() for kw in ETF_KEYWORDS)

    def is_preferred(self, name: str) -> bool:
        """우선주 필터: 종목명 기반 (1우/2우/3우/우B/우C 모두 포함)"""
        return (name.endswith("우") or
                "우B" in name or
                "우C" in name or
                "1우" in name or
                "2우" in name or
                "3우" in name)

    def top10(self) -> list:
        scored = []
        for stock in self.stocks:
            code = str(stock.get("code", "")).zfill(6)
            name = stock.get("name", "")
            if not code or not name:
                continue
            if self.is_etf(name):
                continue
            if self.is_preferred(name):
                continue
            scored.append((self.score(stock), stock))

        scored.sort(key=lambda x: x[0], reverse=True)

        result = []
        for rank, (s, stock) in enumerate(scored[:10], 1):
            code = str(stock.get("code", "")).zfill(6)
            result.append({
                "rank":       rank,
                "code":       code,
                "name":       stock.get("name", ""),
                "score":      s,
                "price":      int(safe_float(stock.get("close"))),
                "change_pct": round(safe_float(stock.get("change_rate")), 2),
                "volume":     int(safe_float(stock.get("volume"))),
                "flow_net":   round(self.flow_map.get(code, 0.0), 2),
                "reason":     self.build_reason(stock)
            })
        return result


class RegimeEngine:

    def __init__(self):
        self.prev_score     = 0.0
        self.lock_count     = 0
        self.lock_candidate = "SIDEWAY"
        self.last_regime    = "SIDEWAY"
        self._load_cache()

    def _load_cache(self):
        try:
            with open(REGIME_CACHE, "r", encoding="utf-8") as f:
                c = json.load(f)
            self.prev_score     = float(c.get("prev_score", 0.0))
            self.lock_count     = int(c.get("lock_count", 0))
            self.lock_candidate = c.get("lock_candidate", "SIDEWAY")
            self.last_regime    = c.get("last_regime", "SIDEWAY")
            print(f"[ENGINE] cache loaded → prev={self.prev_score:.3f}")
        except Exception:
            print("[ENGINE] cache init")

    def _save_cache(self, score: float, regime: str, confidence: float):
        with open(REGIME_CACHE, "w", encoding="utf-8") as f:
            json.dump({
                "prev_score":      score,
                "lock_count":      self.lock_count,
                "lock_candidate":  self.lock_candidate,
                "last_regime":     regime,
                "last_confidence": confidence,
                "updated_at":      datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)

    def load_market_flow(self) -> dict:
        try:
            with open("market_flow.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            print("[WARN] market_flow.json 로드 실패")
            return {}

    def load_stock_data(self) -> list:
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
            stocks = raw.get("all", [])
            print(f"[DATA] {len(stocks)}종목 로드")
            return stocks
        except Exception as e:
            print(f"[WARN] data.json 로드 실패: {e}")
            return []

    def compute_flow_score(self, flow: dict) -> float:
        history = flow.get("history", [])
        if history:
            kp_f = ema([h["scores"].get("KOSPI_foreign", 0) for h in history])
            kp_i = ema([h["scores"].get("KOSPI_institution", 0) for h in history])
            kq_f = ema([h["scores"].get("KOSDAQ_foreign", 0) for h in history])
            kq_i = ema([h["scores"].get("KOSDAQ_institution", 0) for h in history])
        else:
            kp_f = flow.get("KOSPI_foreign", {}).get("score", 0)
            kp_i = flow.get("KOSPI_institution", {}).get("score", 0)
            kq_f = flow.get("KOSDAQ_foreign", {}).get("score", 0)
            kq_i = flow.get("KOSDAQ_institution", {}).get("score", 0)
        return float(kp_f * 0.36 + kp_i * 0.24 + kq_f * 0.24 + kq_i * 0.16)

    def compute_regime(self, flow: float) -> dict:
        today = flow
        final = 0.7 * today + 0.3 * self.prev_score
        vol   = abs(today - self.prev_score)
        final = max(-1.0, min(1.0, final + vol * 0.2))

        print(f"[ENGINE] today={today:.3f} prev={self.prev_score:.3f} vol={vol:.3f} final={final:.3f}")

        candidate = ("UPTREND"   if final >  0.5
                else "DOWNTREND" if final < -0.5
                else "SIDEWAY")

        if candidate == self.lock_candidate:
            self.lock_count += 1
        else:
            self.lock_candidate = candidate
            self.lock_count = 1

        regime = (candidate if self.lock_count >= dynamic_lock_threshold(final)
                  else self.last_regime)

        confidence       = dynamic_confidence(final, vol)
        self.prev_score  = final
        self.last_regime = regime

        return {
            "regime":     regime,
            "confidence": confidence,
            "score":      round(final, 4),
            "volatility": round(vol, 4)
        }

    def save_result(self, res: dict, flow: dict, top10: list):
        def fs(k):
            return flow.get(k, {}).get("score", 0)

        output = {
            "date":       datetime.now().strftime("%Y-%m-%d"),
            "regime":     res["regime"],
            "confidence": res["confidence"],
            "score":      res["score"],
            "volatility": res["volatility"],
            "flow_summary": {
                "KOSPI_foreign":      fs("KOSPI_foreign"),
                "KOSPI_institution":  fs("KOSPI_institution"),
                "KOSDAQ_foreign":     fs("KOSDAQ_foreign"),
                "KOSDAQ_institution": fs("KOSDAQ_institution"),
            },
            "top10":      top10,
            "updated_at": datetime.now().isoformat()
        }

        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"[DONE] {res['regime']} / confidence={res['confidence']}")
        print("[TOP10]")
        for item in top10:
            print(f"  {item['rank']:>2}. {item['code']} {item['name']:<12} "
                  f"score={item['score']:.1f} | {item['reason']}")

    def run(self):
        flow   = self.load_market_flow()
        stocks = self.load_stock_data()

        res   = self.compute_regime(self.compute_flow_score(flow))

        if stocks:
            scorer = StockScorer(stocks, flow, res["regime"])
            top10  = scorer.top10()
            print(f"[SCORER] {len(stocks)}종목 스캔 → Top10 완료")
        else:
            top10 = []
            print("[WARN] data.json 비어있음 → Top10 없음")

        self.save_result(res, flow, top10)
        self._save_cache(res["score"], res["regime"], res["confidence"])


if __name__ == "__main__":
    RegimeEngine().run()
