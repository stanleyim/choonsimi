"""
news_fetch.py — v2 FINAL (ENGINE v37 COMPATIBLE)
키워드 / 종목 매핑 확장 + 안전 처리
"""

import time

BASE = "https://news.google.com/rss/search?q="

# ── 섹터 키워드 ──────────────────────────────────────
KEYWORDS = [
    # 산업/테마
    "반도체", "AI인공지능", "2차전지", "전기차",
    "바이오", "원전", "방산", "조선",
    "로봇", "자율주행", "신재생에너지", "수소",
    # 매크로
    "금리", "환율", "CPI", "FOMC",
    "코스피", "외국인매수", "기관매수",
    # 개별 종목
    "삼성전자", "SK하이닉스", "현대차",
    "LG에너지솔루션", "포스코", "한화에어로스페이스",
]

# ── 긍정 / 부정 키워드 ───────────────────────────────
POS = [
    "상승", "급등", "호재", "개선", "돌파", "최고", "흑자전환",
    "수주", "계약", "매수", "증가", "성장", "신고가", "강세",
]
NEG = [
    "하락", "급락", "우려", "적자", "리스크", "손실", "취소",
    "소송", "감소", "침체", "약세", "매도", "불안", "위기",
]

# ── 종목명 → 코드 매핑 (30종목) ─────────────────────
CODE_MAP = {
    "삼성전자":         "005930",
    "SK하이닉스":       "000660",
    "현대차":           "005380",
    "기아":             "000270",
    "LG에너지솔루션":   "373220",
    "삼성바이오로직스": "207940",
    "셀트리온":         "068270",
    "NAVER":            "035420",
    "카카오":           "035720",
    "포스코":           "005490",
    "LG화학":           "051910",
    "삼성SDI":          "006400",
    "현대모비스":       "012330",
    "KB금융":           "105560",
    "신한지주":         "055550",
    "하나금융":         "086790",
    "LG전자":           "066570",
    "한화에어로스페이스": "012450",
    "HD현대중공업":     "329180",
    "두산에너빌리티":   "034020",
    "에코프로":         "086520",
    "에코프로비엠":     "247540",
    "포스코퓨처엠":     "003670",
    "한화오션":         "042660",
    "SK이노베이션":     "096770",
    "고려아연":         "010130",
    "HMM":              "011200",
    "대한항공":         "003490",
    "한미약품":         "128940",
    "크래프톤":         "259960",
}


def fetch_titles(keyword: str) -> list:
    """Google RSS에서 최근 1일 뉴스 제목 수집."""
    try:
        import feedparser
        url  = f"{BASE}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        return [e.title for e in feed.entries[:15]]
    except ImportError:
        print("  [NEWS] feedparser 미설치 → skip")
        return []
    except Exception as e:
        print(f"  [NEWS] {keyword} 수집 실패: {e}")
        return []


def score_title(title: str) -> float:
    """제목 감성 점수 (-N ~ +N)."""
    s = 0
    for p in POS:
        if p in title: s += 1
    for n in NEG:
        if n in title: s -= 1
    return float(s)


def map_code(title: str):
    """제목에서 종목코드 추출. 없으면 None."""
    for name, code in CODE_MAP.items():
        if name in title:
            return code
    return None


def run() -> list:
    """
    전체 키워드 RSS 수집 → 종목별 뉴스 점수 합산 반환.
    반환: [{"code": "005930", "score": 2.0}, ...]
    feedparser 미설치 시 빈 리스트 반환 (파이프라인 안 죽음).
    """
    print("[NEWS START]")

    rows = []
    for kw in KEYWORDS:
        for title in fetch_titles(kw):
            code = map_code(title)
            if not code:
                continue
            rows.append({"code": code, "score": score_title(title)})
        time.sleep(0.2)

    if not rows:
        print("[NEWS] 수집 결과 없음 → 빈 리스트 반환")
        return []

    try:
        import pandas as pd
        df  = pd.DataFrame(rows)
        out = df.groupby("code", as_index=False)["score"].sum()
        print(f"[NEWS DONE] {len(out)}종목 점수화")
        return out.to_dict("records")
    except Exception as e:
        print(f"[NEWS] 집계 오류: {e}")
        return []


if __name__ == "__main__":
    result = run()
    for r in result:
        print(r)
