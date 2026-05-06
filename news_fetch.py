"""
news_fetch.py — v4.1 FINAL
- Fix: 노이즈 뉴스 제거 (score 절대값 필터)
- 기존 구조 유지
"""

import os
import json
import time

BASE = "https://news.google.com/rss/search?q="

KEYWORDS = [
    "반도체","AI인공지능","2차전지","전기차","바이오","원전","방산","조선",
    "로봇","자율주행","신재생에너지","수소",
    "금리","환율","CPI","FOMC",
    "코스피","외국인매수","기관매수",
    "삼성전자","SK하이닉스","현대차",
    "LG에너지솔루션","포스코","한화에어로스페이스",
]

POS = ["상승","급등","호재","개선","돌파","최고","흑자전환","수주","계약","매수","증가","성장","신고가","강세"]
NEG = ["하락","급락","우려","적자","리스크","손실","취소","소송","감소","침체","약세","매도","불안","위기"]

TOP_VOLUME_N = 50


def _normalize_code(code: str) -> str:
    return str(code).replace(".0","").strip().zfill(6)


def load_code_map():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "data.json")

        if not os.path.exists(path):
            return {}

        with open(path,"r",encoding="utf-8") as f:
            raw = json.load(f)

        m = {}
        for i in raw.get("all",[]):
            if i.get("name") and i.get("code"):
                m[i["name"]] = _normalize_code(i["code"])
        return m

    except:
        return {}


def load_top_volume_names(n=TOP_VOLUME_N):
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root,"data.json")

        with open(path,"r",encoding="utf-8") as f:
            raw = json.load(f)

        items = sorted(raw.get("all",[]), key=lambda x:int(x.get("volume",0)), reverse=True)
        return [i["name"] for i in items[:n] if i.get("name")]

    except:
        return []


def fetch_titles(keyword):
    try:
        import feedparser
        url = f"{BASE}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        return [getattr(e,"title","") for e in feed.entries[:15]]
    except:
        return []


def score_title(title):
    s = 0
    for p in POS:
        if p in title: s += 1
    for n in NEG:
        if n in title: s -= 1
    return float(s)


def map_code(title, code_map):
    for name, code in code_map.items():
        if name in title:
            return code
    return None


def run():
    print("[NEWS START]")

    code_map = load_code_map()
    rows = []

    # 1) 키워드
    for kw in KEYWORDS:
        for t in fetch_titles(kw):
            c = map_code(t, code_map)
            if c:
                rows.append({"code":c,"score":score_title(t)})
        time.sleep(0.2)

    # 2) 거래량 상위
    for name in load_top_volume_names():
        code = code_map.get(name)
        if not code:
            continue
        for t in fetch_titles(name):
            rows.append({"code":code,"score":score_title(t)})
        time.sleep(0.2)

    if not rows:
        print("[NEWS] empty")
        return []

    import pandas as pd
    df = pd.DataFrame(rows)
    df["code"] = df["code"].astype(str).str.zfill(6)

    out = df.groupby("code",as_index=False)["score"].sum()

    # ✅ 핵심 개선
    out = out[out["score"].abs() >= 2]

    print(f"[NEWS DONE] {len(out)}종목")
    return out.to_dict("records")
