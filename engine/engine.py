"""engine/engine.py — v24.6_FINAL"""
import pandas as pd
import numpy as np
import json

ENGINE_VERSION = "24.6_FINAL"
IC_WINDOW = 5

def zscore(x):
    x = pd.Series(x, dtype=float)
    mu, sd = x.mean(skipna=True), x.std(skipna=True)
    if sd is None or sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=x.index)
    return (x - mu) / sd

def compute_score(df):
    has_flow = (df["foreign_net"].abs() > 0).any() or (df["inst_net"].abs() > 0).any()
    
    if has_flow:
        w_flow, w_mom, w_dart = 0.5, 0.3, 0.2
        print(" [SCORE] 가중치: flow=50%, mom=30%, dart=20%")
    else:
        w_flow, w_mom, w_dart = 0.0, 0.5, 0.5
        print(" [SCORE] 수급 데이터 없음 → mom=50%, dart=50% 로 조정")
    
    df["ret5"] = df.groupby("code")["close"].pct_change(periods=IC_WINDOW)
    df["mom_z"] = zscore(df["ret5"])
    df["flow"] = df["foreign_net"] + df["inst_net"]
    df["flow_z"] = zscore(df["flow"])
    df["dart_z"] = zscore(df["dart_score"])
    df["score"] = w_flow * df["flow_z"] + w_mom * df["mom_z"] + w_dart * df["dart_z"]
    df["score"] = df["score"].fillna(-999.0)
    return df, has_flow

def calc_ic(df):
    ret = df.groupby("code")["close"].pct_change(periods=IC_WINDOW)
    score = df["score"]
    valid = ret.notna() & score.notna() & (score!= -999.0)
    if valid.sum() < 30:
        return None
    return np.corrcoef(score[valid], ret[valid])[0, 1]

def main():
    print(f"[ENGINE START] {ENGINE_VERSION}")
    
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data["all"])
        date_str = data["date"]
        data_source = data.get("source", "unknown")
        print(f"[DATA] {len(df)}개 종목 로드 / 기준일: {date_str} / source: {data_source}")
    except Exception as e:
        print(f" data.json 로드 실패: {e}")
        return
    
    df, has_flow = compute_score(df)
    ic = calc_ic(df)
    
    top10 = df.nlargest(10, "score")[["code", "name", "market", "close", "score"]].copy()
    top10["rank"] = range(1, 11)
    top10["score"] = top10["score"].round(4)
    top10["close"] = top10["close"].round(0).astype(int)
    records = top10.to_dict(orient="records")
    
    quality = "full" if has_flow else ("sample" if data_source == "sample" else "partial")
    
    result = {
        "version": ENGINE_VERSION,
        "biz_day": date_str,
        "data_quality": quality,
        "data_source": data_source,
        "ic": round(ic, 4) if ic is not None else None,
        "ic_window": IC_WINDOW,
        "count": len(df),
        "top10": records,
    }
    
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"[ENGINE DONE] TOP1: {records[0]['name']} / score {records[0]['score']}")
    print(f"[DATA QUALITY] {quality} / IC={result['ic']}")

if __name__ == "__main__":
    main()
