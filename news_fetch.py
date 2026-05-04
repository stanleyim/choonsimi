"""
news_fetch.py — KRX News Signal Engine (NAVER RSS 기반)
"""

import requests
import pandas as pd
import feedparser
import time
from datetime import datetime

# =========================
# 설정
# =========================
BASE_URL = "https://news.google.com/rss/search?q="
KOREA = "KR"

# 간단 테마 키워드 (KRX 특화)
KEYWORDS = [
    "반도체", "AI", "2차전지", "전기차",
    "금리", "부동산", "바이오", "원전",
    "삼성전자", "SK하이닉스", "현대차"
]


# =========================
# 뉴스 수집
# =========================
def fetch_news(keyword, limit=20):

    url = f"{BASE_URL}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"

    feed = feedparser.parse(url)

    articles = []

    for entry in feed.entries[:limit]:
        articles.append({
            "title": entry.title,
            "published": entry.published if hasattr(entry, "published") else "",
            "link": entry.link,
            "keyword": keyword
        })

    return articles


# =========================
# 감성/중요도 점수 (단순화 핵심)
# =========================
def score_news(title):

    score = 0

    # positive keywords
    pos_words = ["상승", "급등", "호재", "증가", "개선", "흑자", "돌파"]

    # negative keywords
    neg_words = ["하락", "급락", "적자", "우려", "감소", "리스크"]

    for w in pos_words:
        if w in title:
            score += 1

    for w in neg_words:
        if w in title:
            score -= 1

    return score


# =========================
# 종목 매칭
# =========================
def map_to_stock(title):

    # 아주 단순 but effective
    mapping = {
        "삼성전자": "005930",
        "SK하이닉스": "000660",
        "현대차": "005380"
    }

    for k, v in mapping.items():
        if k in title:
            return v

    return None


# =========================
# MAIN
# =========================
def run():

    print("[NEWS FETCH START]")

    all_news = []

    for kw in KEYWORDS:

        print(f"[FETCH] {kw}")

        articles = fetch_news(kw)

        for a in articles:

            stock_code = map_to_stock(a["title"])
            sentiment = score_news(a["title"])

            all_news.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "keyword": kw,
                "title": a["title"],
                "stock": stock_code,
                "score": sentiment
            })

        time.sleep(0.5)  # rate limit 방어

    df = pd.DataFrame(all_news)

    # 종목별 뉴스 score 집계
    result = df.groupby("stock")["score"].sum().reset_index()

    result = result.dropna()

    print("[NEWS DONE]")

    return result.to_dict("records")


if __name__ == "__main__":
    run()
