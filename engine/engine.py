"""
RegimeEngine v6.1 FINAL — Production Grade
────────────────────────────────────────
✔ Soft Penalty (과열 감쇠)
✔ Flow 정규화 복원 (핵심)
✔ Tracker (성능 측정 + 누적 저장)
✔ Report (result.json 포함)
✔ Date Filter (당일만)
✔ 안전성 패치 완료
────────────────────────────────────────
"""

import json, math, os, pandas as pd
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

SIGNAL_HISTORY = "signal_history.csv"
PERF_LOG = "performance_log.json"

TOP_N = 20

# 가중치
W_FLOW, W_MOM, W_VOL, W_FUND, W_NEWS = 0.30, 0.25, 0.15, 0.15, 0.15

# ───────── 유틸 ─────────
def safe_float(v, d=0.0):
    try: return float(v)
    except: return d

def tanh_norm(v):
    return (math.tanh(v) + 1) / 2

def zscore_norm(v, m, s):
    return tanh_norm((v - m) / s) if s > 0 else 0.5


# ───────── Scorer ─────────
class StockScorer:
    def __init__(self, stocks, flow, regime, fund, news):
        self.stocks = stocks
        self.flow = flow
        self.regime = regime
        self.fund = fund
        self.news = news

        self.flow_map = self._flow_map()
        self.flow_max = max(abs(v) for v in self.flow_map.values()) or 1.0  # ✅ 최적화

        vols = [safe_float(s.get("volume")) for s in stocks]
        self.vol_mean = sum(vols)/len(vols) if vols else 1
        self.vol_std = (sum((v-self.vol_mean)**2 for v in vols)/len(vols))**0.5 if vols else 1

        chg = [safe_float(s.get("change_rate")) for s in stocks]
        self.chg_mean = sum(chg)/len(chg) if chg else 0
        self.chg_std = (sum((c-self.chg_mean)**2 for c in chg)/len(chg))**0.5 if chg else 1

    def _flow_map(self):
        fm = {}
        for seg, w in [
            ("KOSPI_foreign",0.36),("KOSPI_institution",0.24),
            ("KOSDAQ_foreign",0.24),("KOSDAQ_institution",0.16)
        ]:
            for r in self.flow.get(seg, {}).get("rows", []):
                c = str(r.get("code","")).zfill(6)
                fm[c] = fm.get(c,0) + safe_float(r.get("net")) * w
        return fm

    def score(self, s):
        code = str(s.get("code","")).zfill(6)
        chg  = safe_float(s.get("change_rate"))
        vol  = safe_float(s.get("volume"))
        fd   = self.fund.get(code, {})
        news = self.news.get(code, 0)

        # 🔥 Soft Penalty
        penalty = 1.0
        if chg > 10:
            penalty = max(0.65, 1 - (chg - 10) * 0.035)

        raw = (
            tanh_norm(self.flow_map.get(code,0)/self.flow_max * 3) * W_FLOW +
            zscore_norm(chg, self.chg_mean, self.chg_std) * W_MOM * penalty +
            zscore_norm(vol, self.vol_mean, self.vol_std) * W_VOL +
            (tanh_norm(safe_float(fd.get("roe"))/10) if fd else 0.5) * W_FUND +
            tanh_norm(news) * W_NEWS
        ) * (1.05 if self.regime=="UPTREND" else 0.95 if self.regime=="DOWNTREND" else 1.0) * 100

        return round(max(0,min(100,raw)),2)

    def top_n(self):
        scored = [(self.score(s), s) for s in self.stocks]
        scored.sort(reverse=True, key=lambda x:x[0])

        out = []
        for i,(sc, s) in enumerate(scored[:TOP_N],1):
            out.append({
                "rank": i,
                "code": str(s.get("code","")).zfill(6),
                "name": s.get("name",""),
                "score": sc,
                "price": int(safe_float(s.get("close"))),
                "change_pct": safe_float(s.get("change_rate"))
            })
        return out


# ───────── Engine ─────────
class RegimeEngine:

    def load_json(self, f):
        try:
            with open(f, encoding="utf-8-sig") as fp:
                return json.load(fp)
        except:
            return {}

    def load_stock_data(self):
        try:
            df = pd.read_csv("history.csv", dtype={"code":str}, encoding="utf-8-sig")
            today = datetime.now(KST).strftime("%Y-%m-%d")
            df = df[df["date"] == today]  # ✅ 핵심
            return df.to_dict("records")
        except:
            return []

    def compute_regime(self, flow):
        segs = ["KOSPI_foreign","KOSPI_institution","KOSDAQ_foreign","KOSDAQ_institution"]
        score = sum(flow.get(s,{}).get("score",0) for s in segs)/4
        regime = "UPTREND" if score>0.5 else "DOWNTREND" if score<-0.5 else "SIDEWAY"
        return {"regime":regime,"confidence":round(abs(score),2)}

    # ───── Tracker ─────
    def verify(self):
        try:
            hist = pd.read_csv("history.csv", dtype={"code":str}, encoding="utf-8-sig")
            hist["code"] = hist["code"].str.zfill(6)
            price_map = dict(zip(hist["code"], hist["close"]))

            sig = pd.read_csv(SIGNAL_HISTORY, encoding="utf-8-sig")
            y = (datetime.now(KST)-timedelta(days=1)).strftime("%Y-%m-%d")
            sig = sig[sig["date"]==y]
            if sig.empty: return {"win_rate":0,"avg_return":0,"top5_return":0}

            hits,total,avg = 0,len(sig),0
            top5_rets, new_logs = [], []

            for _,r in sig.iterrows():
                code = str(r["code"]).zfill(6)
                entry = safe_float(r.get("price"))
                exitp = safe_float(price_map.get(code))

                if entry>0:
                    ret = (exitp-entry)/entry*100
                    avg += ret
                    if ret>0: hits+=1
                    if r["rank"]<=5: top5_rets.append(ret)

                    new_logs.append({"date":y,"code":code,"rank":r["rank"],"return_pct":round(ret,2)})

            old = self.load_json(PERF_LOG)
            if not isinstance(old,list): old=[]
            existing = {(d["date"],d["code"]) for d in old}
            filtered = [l for l in new_logs if (l["date"],l["code"]) not in existing]
            old.extend(filtered)

            with open(PERF_LOG,"w",encoding="utf-8-sig") as f:
                json.dump(old[-1000:],f,indent=2,ensure_ascii=False)

            return {
                "win_rate": round(hits/total*100,1),
                "avg_return": round(avg/total,2),
                "top5_return": round(sum(top5_rets)/len(top5_rets),2) if top5_rets else 0
            }
        except:
            return {"win_rate":0,"avg_return":0,"top5_return":0}

    def summarize(self):
        try:
            log = self.load_json(PERF_LOG)
            if not log: return {"win_rate":0,"avg_return":0,"top5_return":0}
            df = pd.DataFrame(log)
            df["rank"] = df["rank"].astype(int)
            return {
                "win_rate": round((df["return_pct"]>0).mean()*100,1),
                "avg_return": round(df["return_pct"].mean(),2),
                "top5_return": round(df[df["rank"]<=5]["return_pct"].mean(),2)
            }
        except:
            return {"win_rate":0,"avg_return":0,"top5_return":0}

    def save_signal_history(self, top, regime):
        today = datetime.now(KST).strftime("%Y-%m-%d")
        df = pd.DataFrame([{"date":today,"regime":regime,**t} for t in top])
        try:
            old = pd.read_csv(SIGNAL_HISTORY, encoding="utf-8-sig")
            old = old[old["date"]!=today]
            df = pd.concat([old,df])
        except: pass
        df.to_csv(SIGNAL_HISTORY,index=False,encoding="utf-8-sig")

    def run(self):
        flow = self.load_json("market_flow.json")
        news = self.load_json("news_scores.json").get("scores",{})

        raw_fund = self.load_json("fundamental.json").get("stocks",[])
        fund = {str(s.get("code","")).zfill(6):s for s in raw_fund}

        stocks = self.load_stock_data()
        reg = self.compute_regime(flow)

        scorer = StockScorer(stocks, flow, reg["regime"], fund, news)
        top = scorer.top_n()

        perf_today = self.verify()
        perf_total = self.summarize()

        self.save_signal_history(top, reg["regime"])

        result = {
            "date": datetime.now(KST).strftime("%Y-%m-%d"),
            "regime": reg["regime"],
            "confidence": reg["confidence"],
            "top20": top,
            "performance_today": perf_today,
            "performance_total": perf_total
        }

        with open("result.json","w",encoding="utf-8-sig") as f:
            json.dump(result,f,indent=2,ensure_ascii=False)

        print("[REPORT]", perf_today)
        print("[DONE]", reg["regime"], "Top:", len(top))


if __name__ == "__main__":
    RegimeEngine().run()
