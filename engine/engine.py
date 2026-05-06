import json
import math
import os
from datetime import datetime


class RegimeEngine:

    def __init__(self):
        self.last_regime = "SIDEWAY"
        self.last_confidence = 0.6

    # ✅ 반환 타입 dict로 통일
    def load_market_flow(self) -> dict:
        try:
            with open("market_flow.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                print("[ENGINE] ⚠️ market_flow.json 구조 비정상 → 빈 dict 사용")
                return {}
            return data
        except FileNotFoundError:
            print("[ENGINE] ⚠️ market_flow.json 없음 → 빈 dict 사용")
            return {}
        except Exception as e:
            print(f"[ENGINE] ⚠️ market_flow.json 로드 실패: {e}")
            return {}

    # ✅ 순매수 수량 리스트 → -1~1 정규화
    def normalize_flow(self, rows: list) -> float:
        if not rows:
            return 0.0
        total = sum(r.get("net", 0) for r in rows)
        # tanh로 -1~1 압축 (단위: 만주)
        return math.tanh(total / 1_000_000)

    # ✅ market_flow → 종합 flow 스코어
    def compute_flow_score(self, flow_data: dict) -> float:
        kp_f = self.normalize_flow(flow_data.get("KOSPI_foreign", []))
        kp_i = self.normalize_flow(flow_data.get("KOSPI_institution", []))
        kq_f = self.normalize_flow(flow_data.get("KOSDAQ_foreign", []))
        kq_i = self.normalize_flow(flow_data.get("KOSDAQ_institution", []))

        # KOSPI 60%, KOSDAQ 40% / 외국인 60%, 기관 40%
        kospi  = kp_f * 0.6 + kp_i * 0.4
        kosdaq = kq_f * 0.6 + kq_i * 0.4
        score  = kospi * 0.6 + kosdaq * 0.4

        print(f"[ENGINE] flow → KOSPI={kospi:.3f} KOSDAQ={kosdaq:.3f} 종합={score:.3f}")
        return score

    def compute_regime(self, momentum: float, flow: float) -> dict:
        score = momentum * 0.6 + flow * 0.4

        if score > 0.5:
            regime, confidence = "UPTREND", 0.75
        elif score < -0.5:
            regime, confidence = "DOWNTREND", 0.75
        else:
            regime, confidence = "SIDEWAY", 0.70

        self.last_regime = regime
        self.last_confidence = confidence

        print(f"[ENGINE] regime={regime} / score={score:.3f} / confidence={confidence}")
        return {"regime": regime, "confidence": confidence}

    # ✅ momentum 로드 (fetch_data.py가 남긴 파일)
    def load_momentum(self) -> float:
        try:
            with open("market_data.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            mom = float(data.get("momentum", 0.0))
            print(f"[ENGINE] momentum={mom:.3f}")
            return mom
        except Exception as e:
            print(f"[ENGINE] ⚠️ momentum 로드 실패: {e} → 0.0 사용")
            return 0.0

    # ✅ result.json 저장
    def save_result(self, regime_result: dict, flow_data: dict):
        output = {
            "regime":     regime_result["regime"],
            "confidence": regime_result["confidence"],
            "flow_summary": {
                "KOSPI_foreign":     self.normalize_flow(flow_data.get("KOSPI_foreign", [])),
                "KOSPI_institution": self.normalize_flow(flow_data.get("KOSPI_institution", [])),
                "KOSDAQ_foreign":    self.normalize_flow(flow_data.get("KOSDAQ_foreign", [])),
                "KOSDAQ_institution":self.normalize_flow(flow_data.get("KOSDAQ_institution", [])),
            },
            "updated_at": datetime.now().isoformat()
        }
        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print("[ENGINE] ✅ result.json 저장 완료")

    def run(self):
        print(f"[ENGINE START] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        flow_data = self.load_market_flow()
        flow      = self.compute_flow_score(flow_data)
        momentum  = self.load_momentum()

        regime_result = self.compute_regime(momentum, flow)
        self.save_result(regime_result, flow_data)


if __name__ == "__main__":
    RegimeEngine().run()
