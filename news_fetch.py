"""
news_fetch.py — FINAL (ENGINE v37 COMPATIBLE)
"""

import feedparser
import pandas as pd
import time
from datetime import datetime

BASE = "https://news.google.com/rss/search?q="

KEYWORDS = [
    "반도체","AI","2차전지","전기차",
    "바이오","원전","방산","조선",
    "금리","환율","CPI","FOMC",
    "삼성전자","SK하이닉스","현대차"
]


def fetch(keyword):
    url = f"{BASE}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    return [e.title for e in feed.entries[:15]]


def score(title):

    pos = ["상승","급등","호재","개선","돌파"]
    neg = ["하락","급락","우려","적자","리스크"]

    s = 0
    for p in pos:
        if p in title: s += 1
    for n in neg:
        if n in title: s -= 1

    return s


def map_code(title):

    m = {
        "삼성전자":"005930",
        "SK하이닉스":"000660",
        "현대차":"005380"
    }

    for k,v in m.items():
        if k in title:
            return v

    return None


def run():

    print("[NEWS START]")

    rows = []

    for kw in KEYWORDS:

        for t in fetch(kw):

            code = map_code(t)
            if not code:
                continue

            rows.append({
                "code": code,
                "score": score(t)
            })

        time.sleep(0.2)

    df = pd.DataFrame(rows)

    if len(df) == 0:
        return []

    out = df.groupby("code", as_index=False)["score"].sum()

    print("[NEWS DONE]")

    return out.to_dict("records")


if __name__ == "__main__":
    run()
