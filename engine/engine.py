"""
RegimeEngine v5.4 — Top 20 Stock Recommender (Patched)
────────────────────────────────────────────────────────────
v5.4 패치 내역 (7개):
  [1] compute_flow_score() else → scores 딕셔너리 직접 참조 (버그 수정)
  [2] stock_flow.json 관련 코드 완전 제거 (market_flow.json만 사용)
  [3] load_stock_data() → history.csv 오늘자 데이터 읽기
  [4] calc_fundamental_score() 클리핑 강화 (ROE±50, debt 0~500, growth ±100)
  [5] TOP_N = 20 (Top10 → Top20)
  [6] score clamp: max(0, min(score, 100))
  [7] signal_history.csv 누적 저장 (백테스트 기반)

3-팩터 → 4-팩터 (v5.4):
  수급      35%  (외국인+기관 net, flow_map)
  모멘텀    30%  (change_rate)
  거래량    20%  (volume)
  펀더멘털  15%  (ROE + 부채비율 + 영업이익성장)

입력: history.csv, market_flow.json, fundamental.json
출력: result.json, regime_cache.json, signal_history.csv
────────────────────────────────────────────────────────────
"""

import json
import math
import os
import pandas as pd
from datetime import datetime, timezone, timedelta

REGIME_CACHE   = "regime_cache.json"
SIGNAL_HISTORY = "signal_history.csv"
EMA_ALPHA      = 0.4
TOP_N          = 20        # [패치5] Top10 → Top20
KST            = timezone(timedelta(hours=9))

W_FLOW        = 0.35
W_MOMENTUM    = 0.30
W_VOLUME      = 0.20
W_FUNDAMENTAL = 0.15

ETF_KEYWORDS = [
    "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF",
    "HANARO","TIMEFOLIO","TREX","SOL","ACE","ETF","ETN","FOCUS","RISE"
]

# 펀더멘털 정규화 기준
ROE_REF    = 15.0
DEBT_REF   = 150.0
GROWTH_REF = 10.0

# [패치4] 클리핑 범위
ROE_CLIP_MIN,    ROE_CLIP_MAX    = -50.0,  50.0
DEBT_CLIP_MIN,   DEBT_CLIP_MAX   =   0.0, 500.0
GROWTH_CLIP_MIN, GROWTH_CLIP_MAX = -100.0, 100.0


# ─────────────────────────── 유틸 ───────────────────────────

def safe_float(v, default=0.0) -> float:
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


def zscore_norm(val: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.5
    return tanh_norm((val - mean) / std)


def dynamic_confidence(score: float, volatility: float) -> float:
    base    = min(0.95, 0.5 + abs(score) * 0.6)
    penalty = min(0.15, volatility * 0.3)
    return round(max(0.50, base - penalty), 3)


def dynamic_lock_threshold(score: float) -> int:
    return 2 if abs(score) > 0.7 else 3


# ──────────────────── 펀더멘털 점수 ─────────────────────────

def calc_fundamental_score(roe: float, debt_ratio: float, op_growth: float) -> float:
    # [패치4] 클리핑 강화
    roe       = max(ROE_CLIP_MIN,    min(ROE_CLIP_MAX,    roe))
    debt_ratio= max(DEBT_CLIP_MIN,   min(DEBT_CLIP_MAX,   debt_ratio))
    op_growth = max(GROWTH_CLIP_MIN, min(GROWTH_CLIP_MAX, op_growth))

    roe_s    = tanh_norm(roe       /  ROE_REF)
    debt_s   = tanh_norm(-debt_ratio / DEBT_REF + 1)
    growth_s = tanh_norm(op_growth /  GROWTH_REF)

    return round(roe_s * 0.40 + debt_s * 0.35 + growth_s * 0.25, 4)


# ──────────────────────── StockScorer ────────────────────────

class StockScorer:

    def __init__(self, stocks: list, flow_data: dict, regime: str, fund_map: dict):
        self.stocks   = stocks
        self.flow     = flow_data
        self.regime   = regime
        self.fund_map = fund_map
        self.flow_map = self._build_flow_map()

        vols = [safe_float(s.get("volume")) for s in stocks
                if safe_float(s.get("volume")) > 0]
        self.vol_mean = sum(vols) / len(vols) if vols else 1.0
        self.vol_std  = (sum((v - self.vol_mean)**2 for v in vols)
                         / len(vols))**0.5 if vols else 1.0

        chgs = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chgs) / len(chgs) if chgs else 0.0
        self.chg_std  = (sum((c - self.chg_mean)**2 for c in chgs)
                         / len(chgs))**0.5 if chgs else 1.0

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

    def _fundamental_score(self, code: str) -> float:
        fd = self.fund_map.get(code)
        if not fd:
            return 0.5  # 데이터 없으면 중립
        return calc_fundamental_score(
            roe        = safe_float(fd.get("roe")),
            debt_ratio = safe_float(fd.get("debt_ratio")),
            op_growth  = safe_float(fd.get("op_growth")),
        )

    def _regime_multiplier(self) -> float:
        return {"UPTREND": 1.05, "DOWNTREND": 0.95}.get(self.regime, 1.0)

    def score(self, stock: dict) -> float:
        code = str(stock.get("code", "")).zfill(6)
        chg  = safe_float(stock.get("change_rate"))
        vol  = safe_float(stock.get("volume"))

        raw = (
            self._flow_score(code)        * W_FLOW        +
            self._momentum_score(chg)     * W_MOMENTUM    +
            self._volume_score(vol)       * W_VOLUME      +
            self._fundamental_score(code) * W_FUNDAMENTAL
        ) * self._regime_multiplier() * 100

        return round(max(0.0, min(100.0, raw)), 2)   # [패치6] score clamp

    def build_reason(self, stock: dict) -> str:
        code = str(stock.get("code", "")).zfill(6)
        net  = self.flow_map.get(code, 0.0)
        chg  = safe_float(stock.get("change_rate"))
        fd   = self.fund_map.get(code)

        # 수급
        if net >= 10:
            flow_txt = f"외국인·기관 강한 순매수(net {net:.0f})"
        elif net > 0:
            flow_txt = f"외국인·기관 순매수(net {net:.0f})"
        elif net == 0:
            flow_txt = "수급 중립"
        else:
            flow_txt = f"외국인·기관 순매도(net {net:.0f})"

        # 모멘텀
        if chg >= 3:
            mom_txt = f"강한 상승(+{chg:.1f}%)"
        elif chg > 0:
            mom_txt = f"소폭 상승(+{chg:.1f}%)"
        elif chg == 0:
            mom_txt = "보합"
        elif chg > -3:
            mom_txt = f"소폭 하락({chg:.1f}%)"
        else:
            mom_txt = f"하락 주의({chg:.1f}%)"

        # 펀더멘털
        if fd:
            roe = safe_float(fd.get("roe"))
            dr  = safe_float(fd.get("debt_ratio"))
            fund_txt = f"ROE {roe:.1f}% / 부채비율 {dr:.0f}%"
        else:
            fund_txt = "펀더멘털 데이터 없음"

        return f"{flow_txt} / {mom_txt} / {fund_txt}"

    def is_etf(self, name: str) -> bool:
        return any(kw in name.upper() for kw in ETF_KEYWORDS)

    def is_preferred(self, name: str) -> bool:
        return (name.endswith("우") or
                "우B" in name or "우C" in name or
                "1우" in name or "2우" in name or "3우" in name)

    def top_n(self) -> list:                            # [패치5] top10 → top_n
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
        for rank, (s, stock) in enumerate(scored[:TOP_N], 1):
            code = str(stock.get("code", "")).zfill(6)
            fd   = self.fund_map.get(code, {})
            result.append({
                "rank":       rank,
                "code":       code,
                "name":       stock.get("name", ""),
                "score":      s,
                "price":      int(safe_float(stock.get("close"))),
                "change_pct": round(safe_float(stock.get("change_rate")), 2),
                "volume":     int(safe_float(stock.get("volume"))),
                "flow_net":   round(self.flow_map.get(code, 0.0), 2),
                "roe":        safe_float(fd.get("roe"))        if fd else None,
                "debt_ratio": safe_float(fd.get("debt_ratio")) if fd else None,
                "op_growth":  safe_float(fd.get("op_growth"))  if fd else None,
                "reason":     self.build_reason(stock),
            })
        return result


# ──────────────────────── RegimeEngine ──────────────────────

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
                "updated_at":      datetime.now(KST).isoformat(),
            }, f, indent=2, ensure_ascii=False)

    # ── 데이터 로드 ─────────────────────────────────────────

    def load_market_flow(self) -> dict:
        try:
            with open("market_flow.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            print("[WARN] market_flow.json 로드 실패")
            return {}

    def load_stock_data(self) -> list:
        """[패치3] history.csv 오늘자 데이터 읽기"""
        try:
            df = pd.read_csv("history.csv", dtype={"code": str})
            df["code"] = df["code"].astype(str).str.zfill(6)

            # 오늘 날짜 필터
            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            if "date" in df.columns:
                df_today = df[df["date"] == today_str]
                if df_today.empty:
                    print(f"[WARN] {today_str} 데이터 없음 → 전체 사용")
                    df_today = df
            else:
                df_today = df

            # 필요 컬럼 확인
            for col in ("close", "volume", "change_rate"):
                if col not in df_today.columns:
                    df_today = df_today.copy()
                    df_today[col] = 0.0

            df_today["change_rate"] = pd.to_numeric(
                df_today["change_rate"], errors="coerce"
            ).fillna(0.0)
            df_today["volume"] = pd.to_numeric(
                df_today["volume"], errors="coerce"
            ).fillna(0)

            stocks = df_today.to_dict("records")
            print(f"[DATA] {len(stocks)}종목 로드 ({today_str})")
            return stocks

        except FileNotFoundError:
            print("[WARN] history.csv 없음")
            return []
        except Exception as e:
            print(f"[WARN] history.csv 로드 실패: {e}")
            return []

    def load_fundamental(self) -> dict:
        """fundamental.json → {code: {roe, debt_ratio, op_growth}}"""
        try:
            with open("fundamental.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
            stocks = raw.get("stocks", [])
            if not stocks:
                print("[WARN] fundamental.json 비어있음 → 중립 처리")
                return {}
            fm = {}
            for s in stocks:
                code = str(s.get("code", "")).zfill(6)
                if code:
                    fm[code] = {
                        "roe":        safe_float(s.get("roe")),
                        "debt_ratio": safe_float(s.get("debt_ratio")),
                        "op_growth":  safe_float(s.get("op_growth")),
                        "net_income": safe_float(s.get("net_income")),
                    }
            print(f"[FUND] {len(fm)}종목 펀더멘털 로드")
            return fm
        except FileNotFoundError:
            print("[WARN] fundamental.json 없음 → 중립 처리")
            return {}
        except Exception as e:
            print(f"[WARN] fundamental.json 로드 실패: {e}")
            return {}

    # ── 레짐 계산 ────────────────────────────────────────────

    def compute_flow_score(self, flow: dict) -> float:
        """[패치1] history 없을 때 scores 딕셔너리 직접 참조"""
        history = flow.get("history", [])
        if history:
            kp_f = ema([h["scores"].get("KOSPI_foreign",      0) for h in history])
            kp_i = ema([h["scores"].get("KOSPI_institution",  0) for h in history])
            kq_f = ema([h["scores"].get("KOSDAQ_foreign",     0) for h in history])
            kq_i = ema([h["scores"].get("KOSDAQ_institution", 0) for h in history])
        else:
            # [패치1] 수정: flow.get("scores",{}) 경로로 접근
            scores = flow.get("scores", {})
            kp_f = scores.get("KOSPI_foreign",      0)
            kp_i = scores.get("KOSPI_institution",  0)
            kq_f = scores.get("KOSDAQ_foreign",     0)
            kq_i = scores.get("KOSDAQ_institution", 0)

        return float(kp_f * 0.36 + kp_i * 0.24 + kq_f * 0.24 + kq_i * 0.16)

    def compute_regime(self, flow: float) -> dict:
        today = flow
        final = 0.7 * today + 0.3 * self.prev_score
        vol   = abs(today - self.prev_score)
        final = max(-1.0, min(1.0, final + vol * 0.2))

        print(f"[ENGINE] today={today:.3f} prev={self.prev_score:.3f} "
              f"vol={vol:.3f} final={final:.3f}")

        candidate = ("UPTREND"   if final >  0.5
                else "DOWNTREND" if final < -0.5
                else "SIDEWAY")

        if candidate == self.lock_candidate:
            self.lock_count += 1
        else:
            self.lock_candidate = candidate
            self.lock_count     = 1

        regime = (candidate if self.lock_count >= dynamic_lock_threshold(final)
                  else self.last_regime)

        confidence       = dynamic_confidence(final, vol)
        self.prev_score  = final
        self.last_regime = regime

        return {
            "regime":     regime,
            "confidence": confidence,
            "score":      round(final, 4),
            "volatility": round(vol, 4),
        }

    # ── 결과 저장 ────────────────────────────────────────────

    def save_result(self, res: dict, flow: dict, top_n: list):
        def fs(k):
            return flow.get(k, {}).get("score", 0)

        output = {
            "date":       datetime.now(KST).strftime("%Y-%m-%d"),
            "regime":     res["regime"],
            "confidence": res["confidence"],
            "score":      res["score"],
            "volatility": res["volatility"],
            "weights": {
                "flow":        W_FLOW,
                "momentum":    W_MOMENTUM,
                "volume":      W_VOLUME,
                "fundamental": W_FUNDAMENTAL,
            },
            "flow_summary": {
                "KOSPI_foreign":      fs("KOSPI_foreign"),
                "KOSPI_institution":  fs("KOSPI_institution"),
                "KOSDAQ_foreign":     fs("KOSDAQ_foreign"),
                "KOSDAQ_institution": fs("KOSDAQ_institution"),
            },
            "top20":      top_n,
            "updated_at": datetime.now(KST).isoformat(),
        }

        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"[DONE] {res['regime']} / confidence={res['confidence']}")
        print(f"[TOP{TOP_N}]")
        for item in top_n:
            roe_str = (f"ROE={item['roe']:.1f}%"
                       if item.get("roe") is not None else "ROE=N/A")
            print(f"  {item['rank']:>2}. {item['code']} {item['name']:<12} "
                  f"score={item['score']:.1f} chg={item['change_pct']:+.2f}% "
                  f"{roe_str} | {item['reason']}")

    def save_signal_history(self, top_n: list, regime: str):
        """[패치7] signal_history.csv 누적 저장 (백테스트 기반 데이터)"""
        today = datetime.now(KST).strftime("%Y-%m-%d")

        rows = []
        for item in top_n:
            rows.append({
                "date":       today,
                "regime":     regime,
                "rank":       item["rank"],
                "code":       item["code"],
                "name":       item["name"],
                "score":      item["score"],
                "price":      item["price"],
                "change_pct": item["change_pct"],
                "flow_net":   item["flow_net"],
                "roe":        item.get("roe", ""),
                "debt_ratio": item.get("debt_ratio", ""),
                "op_growth":  item.get("op_growth", ""),
            })

        df_new = pd.DataFrame(rows)

        if os.path.exists(SIGNAL_HISTORY):
            try:
                df_old = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str})
                # 오늘 날짜 중복 제거 후 append
                df_old = df_old[df_old["date"] != today]
                df_out = pd.concat([df_old, df_new], ignore_index=True)
            except Exception:
                df_out = df_new
        else:
            df_out = df_new

        df_out.to_csv(SIGNAL_HISTORY, index=False, encoding="utf-8-sig")
        print(f"[SIGNAL] {len(rows)}개 → {SIGNAL_HISTORY} 저장 (누적 {len(df_out)}행)")

    # ── 메인 실행 ────────────────────────────────────────────

    def run(self):
        flow     = self.load_market_flow()
        stocks   = self.load_stock_data()
        fund_map = self.load_fundamental()

        res = self.compute_regime(self.compute_flow_score(flow))

        if stocks:
            scorer = StockScorer(stocks, flow, res["regime"], fund_map)
            top_n  = scorer.top_n()
            fund_ok = sum(1 for item in top_n if item.get("roe") is not None)
            print(f"[SCORER] {len(stocks)}종목 스캔 → Top{TOP_N} 완료 "
                  f"(펀더멘털 적용 {fund_ok}/{len(top_n)})")
        else:
            top_n = []
            print("[WARN] 종목 데이터 없음 → Top N 없음")

        self.save_result(res, flow, top_n)
        self.save_signal_history(top_n, res["regime"])   # [패치7]
        self._save_cache(res["score"], res["regime"], res["confidence"])


if __name__ == "__main__":
    RegimeEngine().run()
