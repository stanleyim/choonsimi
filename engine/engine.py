"""
RegimeEngine v6.4.1 — FINAL PRODUCTION HARDENED + ENTRY FILTER
─────────────────────────────────────────────────────
✔ v6.4.0 안정성 전체 유지: 95th percentile, stability smoothing, 7-day gap, KR holiday
✔ ADD: entry_filter (기관급 타이밍 필터) - Flow>0 + Price>0 + Volume 1.3배 + 0.5~6%
✔ ADD: entry_top5, top5_core 분리 출력
✔ ADD: stability_count JSON 출력
✔ FIX: verify() evaluated-only denominator 유지
─────────────────────────────────────────────────────
"""

import json, math, pandas as pd
from datetime import datetime, timedelta, timezone
import holidays

KST = timezone(timedelta(hours=9))
KR_HOLIDAYS = holidays.KR(years=[2025, 2026, 2027])
MAX_GAP_DAYS = 7
STABILITY_BOOST = 1.04 # 어제 TOP20 종목 4% 점수 보정치

SIGNAL_HISTORY = "signal_history.csv"
RESULT_FILE = "result.json"

TOP_N = 20
TOP_CORE = 5

W_FLOW, W_MOM, W_VOL, W_FUND, W_NEWS = 0.30, 0.25, 0.15, 0.15, 0.15

# ─────────────────────────────────────────────
# UTIL
# ─────────────────────────────────────────────
def safe_float(v, d=0.0):
    try:
        return float(v)
    except:
        return d

def tanh_norm(v):
    return (math.tanh(v) + 1) / 2

def zscore_norm(v, m, s):
    return tanh_norm((v - m) / s) if s > 0 else 0.5

def is_common_stock(name, code=""):
    if not name:
        return False

    code = str(code).strip()
    if code.isdigit() and len(code) == 6:
        if code[-1] in ("5", "7", "9"):
            return False

    name = str(name).upper()
    blacklist = [
        "KODEX","TIGER","KBSTAR","ARIRANG","KOSEF","HANARO",
        "TIMEFOLIO","TREX","SOL","ACE","ETF","ETN",
        "LEVERAGE","INVERSE","레버리지","인버스","곱버스",
        "INDEX","지수"
    ]
    return not any(k in name for k in blacklist)

def get_next_trading_day(start_date_str):
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    d = start + timedelta(days=1)

    for _ in range(MAX_GAP_DAYS):
        if d.weekday() < 5 and d not in KR_HOLIDAYS:
            return d.strftime("%Y-%m-%d")
        d += timedelta(days=1)

    return None

# ─────────────────────────────────────────────
# SCORER
# ─────────────────────────────────────────────
class StockScorer:
    def __init__(self, stocks, flow, regime, fund, news, prev_top_codes=None):
        self.stocks = stocks
        self.flow = flow
        self.regime = regime
        self.fund = fund
        self.news = news
        self.prev_top_codes = set(prev_top_codes or [])

        self.flow_map = self._flow_map()

        # 95th percentile outlier-safe scaling
        vals = sorted([abs(v) for v in self.flow_map.values()])
        if vals:
            idx = int(len(vals) * 0.95)
            self.flow_max = vals[max(idx - 1, 0)]
        else:
            self.flow_max = 1.0

        vols = [safe_float(s.get("volume")) for s in stocks]
        self.vol_mean = sum(vols)/len(vols) if vols else 1
        self.vol_std = (sum((v-self.vol_mean)**2 for v in vols)/len(vols))**0.5 if len(vols) > 1 else 1

        chg = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chg)/len(chg) if chg else 0
        self.chg_std = (sum((c-self.chg_mean)**2 for c in chg)/len(chg))**0.5 if len(chg) > 1 else 1

    def _flow_map(self):
        fm = {}
        for seg, w in [
            ("KOSPI_foreign", 0.36),
            ("KOSPI_institution", 0.24),
            ("KOSDAQ_foreign", 0.24),
            ("KOSDAQ_institution", 0.16)
        ]:
            for r in self.flow.get(seg, {}).get("rows", []):
                c = str(r.get("code", "")).zfill(6)
                fm[c] = fm.get(c, 0) + safe_float(r.get("net")) * w
        return fm

    def score(self, s):
        code = str(s.get("code", "")).zfill(6)
        chg = safe_float(s.get("change_rate"))
        vol = safe_float(s.get("volume"))
        fd = self.fund.get(code, {})
        news = self.news.get(code, 0)

        # 10% 초과 급등주 penalty
        penalty = 1.0
        if chg > 10:
            penalty = max(0.65, 1 - (chg - 10) * 0.03)

        mom = zscore_norm(chg, self.chg_mean, self.chg_std) * W_MOM * penalty
        vol_score = zscore_norm(vol, self.vol_mean, self.vol_std) * W_VOL
        fund_score = (tanh_norm(safe_float(fd.get("roe")) / 10) if fd else 0.5) * W_FUND
        flow_score = tanh_norm(self.flow_map.get(code, 0) / self.flow_max * 2.5)

        raw = (
            flow_score * W_FLOW +
            mom + vol_score + fund_score + tanh_norm(news) * W_NEWS
        )

        # regime shift bounded
        if self.regime == "UPTREND":
            raw += 0.05
        elif self.regime == "DOWNTREND":
            raw -= 0.05

        # stability smoothing: 어제 TOP20 종목 4% 보정치
        if code in self.prev_top_codes:
            raw *= STABILITY_BOOST

        return round(max(0, min(100, raw * 100)), 2)

    # Entry Filter: 기관급 진입 타이밍 필터
    def entry_filter(self, s, base_score):
        code = str(s.get("code","")).zfill(6)
        chg = safe_float(s.get("change_rate"))
        vol = safe_float(s.get("volume"))
        flow = self.flow_map.get(code, 0)

        # 1. Flow + Price 방향 일치
        if flow <= 0 or chg <= 0:
            return None
        # 2. Volume 1.3배 이상
        vol_ratio = vol / (self.vol_mean or 1)
        if vol_ratio < 1.3:
            return None
        # 3. Momentum 0.5~6% 범위
        if chg < 0.5 or chg > 6.0:
            return None
        # 4. Hard divergence cut
        if flow * chg < 0:
            return None

        entry_score = (
            base_score * 0.6 +
            min(vol_ratio, 3) * 10 +
            min(abs(flow), 3) * 10
        )
        return round(entry_score, 2)

    def select_top20(self):
        scored = [(self.score(s), s) for s in self.stocks]
        scored.sort(reverse=True, key=lambda x: x[0])

        all_top, core_top = [], []
        entry_candidates = []

        # TOP20 기본 구성
        for i, (sc, s) in enumerate(scored[:TOP_N], 1):
            code = str(s.get("code", "")).zfill(6)
            chg = safe_float(s.get("change_rate"))

            flow_q = 1.0 if self.flow_map.get(code, 0) * chg > 0 else 0.7
            exp = round((sc - 50) * 0.06 * flow_q, 2)

            item = {
                "rank": i,
                "code": code,
                "name": s.get("name", ""),
                "score": sc,
                "price": int(safe_float(s.get("close"))),
                "change_pct": chg,
                "expected_return_5d": exp,
                "roe": self.fund.get(code, {}).get("roe"),
                "debt_ratio": self.fund.get(code, {}).get("debt_ratio")
            }
            all_top.append(item)

            if i <= TOP_CORE and exp > 1.5 and flow_q >= 0.8:
                core_top.append(item)

        # ENTRY 후보 구성 - TOP40까지 봐서 5종목 확보
        for sc, s in scored[:TOP_N*2]:
            es = self.entry_filter(s, sc)
            if es:
                entry_candidates.append((es, s, sc))

        entry_candidates.sort(reverse=True, key=lambda x: x[0])

        entry_top = []
        for i, (es, s, sc) in enumerate(entry_candidates[:5], 1):
            entry_top.append({
                "rank": i,
                "code": str(s.get("code","")).zfill(6),
                "name": s.get("name",""),
                "entry_score": es,
                "base_score": sc,
                "price": int(safe_float(s.get("close"))),
                "change_pct": safe_float(s.get("change_rate"))
            })

        return {
            "top20": all_top,
            "top5_core": core_top if core_top else all_top[:3],
            "entry_top5": entry_top
        }

# ─────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────
class RegimeEngine:

    def load_json(self, f):
        try:
            return json.load(open(f, encoding="utf-8-sig"))
        except:
            return {}

    def load_prev_top20(self):
        """어제 TOP20 코드 리스트 반환"""
        try:
            with open(RESULT_FILE, encoding="utf-8-sig") as f:
                prev = json.load(f)
            return [item["code"] for item in prev.get("top20", [])]
        except:
            return []

    def load_stock_data(self):
        try:
            df = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            df["code"] = df["code"].str.zfill(6)

            today = datetime.now(KST).strftime("%Y-%m-%d")
            return df[df["date"] == today].to_dict("records") if "date" in df.columns else []
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
        score = max(-1, min(1, score))

        return {
            "regime": "UPTREND" if score > 0.3 else ("DOWNTREND" if score < -0.3 else "SIDEWAY"),
            "confidence": round(abs(score), 2)
        }

    def pre_filter(self, stocks, regime):
        base = {
            "UPTREND": (30000, 1000),
            "SIDEWAY": (20000, 1000),
            "DOWNTREND": (40000, 2000)
        }

        min_vol, min_price = base.get(regime, base["SIDEWAY"])

        filtered = [
            s for s in stocks
            if safe_float(s.get("volume")) >= min_vol
            and safe_float(s.get("close")) >= min_price
            and is_common_stock(s.get("name"), s.get("code"))
        ]

        if len(filtered) < 15:
            filtered = filtered[:max(15, len(filtered))]

        return filtered

    def verify(self):
        try:
            hist = pd.read_csv("history.csv", dtype={"code": str}, encoding="utf-8-sig")
            sig = pd.read_csv(SIGNAL_HISTORY, dtype={"code": str}, encoding="utf-8-sig")

            today = datetime.now(KST).strftime("%Y-%m-%d")
            sig_dates = sorted(sig["date"].dropna().unique())
            prev = [d for d in sig_dates if d < today]

            if not prev:
                return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

            y = prev[-1]
            sig = sig[sig["date"] == y]

            next_day = get_next_trading_day(y)
            if not next_day:
                print(f"[VERIFY] 신호일({y}) 이후 {MAX_GAP_DAYS}일 내 거래일 없음 → 평가 스킵")
                return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

            hist_next = hist[hist["date"] == next_day]
            if hist_next.empty:
                print(f"[VERIFY] {next_day} 거래 데이터 없음")
                return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

            price_map = dict(zip(hist_next["code"], hist_next["close"]))

            evaluated, hits, avg, top5 = 0, 0, 0, []

            for _, r in sig.iterrows():
                code = str(r["code"]).zfill(6)
                entry = safe_float(r.get("price"))

                if code in price_map and entry > 0:
                    evaluated += 1
                    ret = (safe_float(price_map[code]) - entry) / entry * 100
                    avg += ret
                    hits += ret > 0

                    if safe_float(r.get("rank")) <= 5:
                        top5.append(ret)

            print(f"[VERIFY] 신호일: {y} → 평가일: {next_day} → 매칭: {evaluated}개")

            return {
                "win_rate": round(hits / evaluated * 100, 1) if evaluated else 0,
                "avg_return": round(avg / evaluated, 2) if evaluated else 0,
                "top5_return": round(sum(top5)/len(top5), 2) if top5 else 0
            }

        except Exception as e:
            print(f"[VERIFY ERROR] {e}")
            return {"win_rate": 0, "avg_return": 0, "top5_return": 0}

    def run(self):
        flow = self.load_json("market_flow.json")
        news = self.load_json("news_scores.json").get("scores", {})

        raw = self.load_json("fundamental.json")
        fund = {str(s.get("code", "")).zfill(6): s for s in (raw if isinstance(raw, list) else raw.get("stocks", []))}

        stocks = self.load_stock_data()
        reg = self.compute_regime(flow)
        stocks = self.pre_filter(stocks, reg["regime"])

        # stability smoothing: 어제 TOP20 로드
        prev_top_codes = self.load_prev_top20()
        stability_count = len(prev_top_codes) # 추가

        if not stocks:
            result = {
                "date": datetime.now(KST).strftime("%Y-%m-%d"),
                "regime": reg["regime"],
                "universe_size": 0,
                "confidence": reg["confidence"],
                "stability_count": 0, # 추가
                "top20": [],
                "top5_core": [],
                "entry_top5": [],
                "performance_today": self.verify()
            }
        else:
            scorer = StockScorer(stocks, flow, reg["regime"], fund, news, prev_top_codes)
            result_data = scorer.select_top20()

            result = {
                "date": datetime.now(KST).strftime("%Y-%m-%d"),
                "regime": reg["regime"],
                "universe_size": len(stocks),
                "confidence": reg["confidence"],
                "stability_count": stability_count, # 추가
                "top20": result_data["top20"],
                "top5_core": result_data["top5_core"],
                "entry_top5": result_data["entry_top5"],
                "performance_today": self.verify()
            }

        with open(RESULT_FILE, "w", encoding="utf-8-sig") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"[DONE] {result.get('regime')} | TOP20:{len(result['top20'])} | ENTRY:{len(result['entry_top5'])} | STABILITY:{stability_count}")

if __name__ == "__main__":
    RegimeEngine().run()
