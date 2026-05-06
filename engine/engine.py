"""
RegimeEngine v4.1 — stabilized production version
────────────────────────────────────────────────────────────
v4 대비 안정화:
  1. atanh 제거 → score는 단일 tanh space 유지 (수치 안정성 확보)
  2. volatility 곱셈 → additive 방식 (과반응 방지)
  3. history EMA 단일화 → snapshot/EMA 혼합 제거
  4. flow score 단순화 → history EMA 기반 일관성 유지
────────────────────────────────────────────────────────────
"""

import json
import math
from datetime import datetime

REGIME_CACHE   = "regime_cache.json"
HISTORY_WINDOW = 5
EMA_ALPHA      = 0.4


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def dynamic_confidence(score: float, volatility: float) -> float:
    base    = min(0.95, 0.5 + abs(score) * 0.6)
    penalty = min(0.15, volatility * 0.3)
    return round(max(0.50, base - penalty), 3)


def dynamic_lock_threshold(score: float) -> int:
    return 2 if abs(score) > 0.7 else 3


def ema(values: list) -> float:
    if not values:
        return 0.0
    e = values[0]
    for v in values[1:]:
        e = EMA_ALPHA * v + (1 - EMA_ALPHA) * e
    return e


# ──────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────
class RegimeEngine:

    def __init__(self):
        self.prev_score     = 0.0
        self.lock_count     = 0
        self.lock_candidate = "SIDEWAY"
        self.last_regime    = "SIDEWAY"
        self._load_cache()

    # ─────────────────────────────
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
                "prev_score": score,
                "lock_count": self.lock_count,
                "lock_candidate": self.lock_candidate,
                "last_regime": regime,
                "last_confidence": confidence,
                "updated_at": datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)

    # ─────────────────────────────
    def load_market_flow(self):
        try:
            with open("market_flow.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except:
            return {}

    # ─────────────────────────────
    def compute_flow_score(self, flow_data: dict) -> float:
        history = flow_data.get("history", [])

        if history:
            kp_f = ema([h["scores"].get("KOSPI_foreign", 0) for h in history])
            kp_i = ema([h["scores"].get("KOSPI_institution", 0) for h in history])
            kq_f = ema([h["scores"].get("KOSDAQ_foreign", 0) for h in history])
            kq_i = ema([h["scores"].get("KOSDAQ_institution", 0) for h in history])
        else:
            kp_f = flow_data.get("KOSPI_foreign", {}).get("score", 0)
            kp_i = flow_data.get("KOSPI_institution", {}).get("score", 0)
            kq_f = flow_data.get("KOSDAQ_foreign", {}).get("score", 0)
            kq_i = flow_data.get("KOSDAQ_institution", {}).get("score", 0)

        kospi  = kp_f * 0.6 + kp_i * 0.4
        kosdaq = kq_f * 0.6 + kq_i * 0.4

        score = kospi * 0.6 + kosdaq * 0.4
        return float(score)

    # ─────────────────────────────
    def load_momentum(self):
        try:
            with open("market_data.json", "r", encoding="utf-8") as f:
                return float(json.load(f).get("momentum", 0))
        except:
            return 0.0

    # ─────────────────────────────
    def compute_regime(self, momentum: float, flow: float):

        today = momentum * 0.6 + flow * 0.4

        # EMA 2-layer
        final = 0.7 * today + 0.3 * self.prev_score

        # volatility (additive)
        vol = abs(today - self.prev_score)
        final = final + (vol * 0.2)

        final = max(-1.0, min(1.0, final))

        print(f"[ENGINE] today={today:.3f} prev={self.prev_score:.3f} vol={vol:.3f} final={final:.3f}")

        # regime
        if final > 0.5:
            candidate = "UPTREND"
        elif final < -0.5:
            candidate = "DOWNTREND"
        else:
            candidate = "SIDEWAY"

        threshold = dynamic_lock_threshold(final)

        if candidate == self.lock_candidate:
            self.lock_count += 1
        else:
            self.lock_candidate = candidate
            self.lock_count = 1

        if self.lock_count >= threshold:
            regime = candidate
        else:
            regime = self.last_regime

        confidence = dynamic_confidence(final, vol)

        self.prev_score  = final
        self.last_regime = regime

        return {
            "regime": regime,
            "confidence": confidence,
            "score": round(final, 4),
            "volatility": round(vol, 4)
        }

    # ─────────────────────────────
    def save_result(self, res, flow):
        def s(k):
            return flow.get(k, {}).get("score", 0)

        with open("result.json", "w", encoding="utf-8") as f:
            json.dump({
                "regime": res["regime"],
                "confidence": res["confidence"],
                "score": res["score"],
                "volatility": res["volatility"],
                "flow_summary": {
                    "KOSPI_foreign": s("KOSPI_foreign"),
                    "KOSPI_institution": s("KOSPI_institution"),
                    "KOSDAQ_foreign": s("KOSDAQ_foreign"),
                    "KOSDAQ_institution": s("KOSDAQ_institution"),
                },
                "updated_at": datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)

    # ─────────────────────────────
    def run(self):
        flow = self.load_market_flow()
        mom  = self.load_momentum()

        res = self.compute_regime(mom, self.compute_flow_score(flow))
        self.save_result(res, flow)

        self._save_cache(res["score"], res["regime"], res["confidence"])

        print(f"[DONE] {res['regime']} / {res['confidence']}")


if __name__ == "__main__":
    RegimeEngine().run()
