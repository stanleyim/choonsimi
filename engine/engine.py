import json

class RegimeEngine:

    def __init__(self):
        self.last_regime = "SIDEWAY"
        self.last_confidence = 0.6

    def load_market_flow(self):
        try:
            with open("market_flow.json", "r") as f:
                return json.load(f)
        except:
            return []

    def compute_regime(self, momentum, flow):

        # KS11 없음 → flow 기반 구조

        score = momentum * 0.6 + flow * 0.4

        if score > 0.5:
            regime = "UPTREND"
            confidence = 0.75
        elif score < -0.5:
            regime = "DOWNTREND"
            confidence = 0.75
        else:
            regime = "SIDEWAY"
            confidence = 0.7

        self.last_regime = regime
        self.last_confidence = confidence

        return {
            "regime": regime,
            "confidence": confidence
        }
