"""
news_fetch.py — KRX News Signal Engine (v2 FINAL - ENGINE v33 COMPATIBLE)
"""

import feedparser
import pandas as pd
import time
from datetime import datetime

# =========================
# 설정
# =========================
BASE_URL = "https://news.google.com/rss/search?q="

# KRX 핵심 확장 키워드
KEYWORDS = [
    # macro
    "금리", "환율", "CPI", "FOMC",

    # sector
    "반도체", "AI", "2차전지", "전기차",
    "바이오", "원전", "방산", "조선",

    # tech theme
    "HBM", "CXL", "GPU", "데이터센터",

    # stock anchors
    "삼성전자", "SK하이닉스", "현대차"
]


# =========================
def fetch_news(keyword, limit=15):

    url = f"{BASE_URL}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"

    feed = feedparser.parse(url)

    items = []

    for e in feed.entries[:limit]:
        items.append(e.title)

    return items


# =========================
def score_news(title):

    pos = ["상승", "급등", "호재", "증가", "개선", "돌파", "흑자"]
    neg = ["하락", "급락", "적자", "우려", "감소", "리스크"]

    s = 0

    for p in pos:
        if p in title:
            s += 1

    for n in neg:
        if n in title:
            s -= 1

    return s


# =========================
def map_stock(title):

    mapping = {
        "삼성전자": "005930",
        "SK하이닉스": "000660",
        "현대차": "005380",
        "LG에너지솔루션": "373220",
        "NAVER": "035420"
    }

    for k, v in mapping.items():
        if k in title:
            return v

    return None


# =========================
def run():

    print("[NEWS FETCH START]")

    rows = []

    for kw in KEYWORDS:

        print(f"[FETCH] {kw}")

        titles = fetch_news(kw)

        for t in titles:

            code = map_stock(t)
            s = score_news(t)

            # noise 제거: 매칭 없는 뉴스 제거
            if code is None:
                continue

            rows.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "code": code,
                "score": s
            })

        time.sleep(0.3)

    if len(rows) == 0:
        print("[NO NEWS DATA]")
        return []

    df = pd.DataFrame(rows)

    # 종목별 합산
    result = df.groupby("code", as_index=False)["score"].sum()

    print("[NEWS DONE]")

    return result.to_dict("records")


if __name__ == "__main__":
    run()
