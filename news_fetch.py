"""
news_fetch.py — v3 FINAL (ENGINE v34 COMPATIBLE)
"""

import feedparser
import pandas as pd
import time
from datetime import datetime

BASE_URL = "https://news.google.com/rss/search?q="

KEYWORDS = [
    "금리","환율","CPI","FOMC",
    "반도체","AI","2차전지","전기차",
    "바이오","원전","방산","조선",
    "HBM","CXL","GPU","데이터센터",
    "삼성전자","SK하이닉스","현대차"
]


def fetch(keyword):
    url = f"{BASE_URL}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    return list({e.title for e in feed.entries[:15]})  # 🔥 dedupe


def score(title):

    pos = ["상승","급등","호재","돌파","흑자","개선"]
    neg = ["하락","급락","적자","리스크","우려"]

    s = 0
    for p in pos:
        if p in title:
            s += 1
    for n in neg:
        if n in title:
            s -= 1

    return s


def map_stock(title):

    m = {
        "삼성전자":"005930",
        "SK하이닉스":"000660",
        "현대차":"005380",
        "LG에너지솔루션":"373220",
        "NAVER":"035420"
    }

    for k,v in m.items():
        if k in title:
            return v
    return None


def run():

    print("[NEWS v3 START]")

    rows = {}

    for kw in KEYWORDS:

        titles = fetch(kw)

        for t in titles:

            code = map_stock(t)
            if not code:
                continue

            s = score(t)

            # aggregate (중복 제거 핵심)
            rows[code] = rows.get(code, 0) + s

        time.sleep(0.2)

    result = [
        {"code": k, "score": v}
        for k,v in rows.items()
    ]

    print("[NEWS DONE]")
    return result


if __name__ == "__main__":
    run()
