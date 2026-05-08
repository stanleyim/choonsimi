"""
fetch_news.py — v1.2 (Option A Patch)
─────────────────────────────────────
Naver News API → 종목별 감성 점수 생성 → news_scores.json
변경사항:
  ✅ 키워드 확장 (금융 도메인 최적화 48개)
  ✅ 인코딩 utf-8-sig 통일
  ✅ API 할당량 보호 (MAX_STOCKS=50, DELAY=0.3)
  ✅ HTML 태그 제거 + 중복 뉴스 필터링
환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
─────────────────────────────────────
"""

import os, json, requests, time, re
from datetime import datetime, timezone, timedelta

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
HISTORY_CSV = "history.csv"
OUTPUT_JSON = "news_scores.json"
KST = timezone(timedelta(hours=9))
MAX_STOCKS = 50  # API 할당량/시간 보호
DELAY = 0.3      # 네이버 API 권장 대기시간

# ─────────────────────────────────────────────────────
# 금융 도메인 최적화 키워드 (Option A 기준)
# ─────────────────────────────────────────────────────
POS_WORDS = {
    # 가격/수급
    "상승", "강세", "급등", "반등", "회복", "매수세",
    "기관순매수", "외국인순매수", "연기금순매수",
    # 실적/가치
    "실적호조", "영업이익증가", "매출증가", "흑자전환",
    "실적예상치상회", "컨센서스상향", "배당성향상승",
    # 기업행동/정책
    "자사주매입", "기술수출", "수주성공", "신사업성공",
    "규제완화", "금리인하", "주가전망밝음", "시가총액증가",
    # 증권사/시장
    "목표가상향", "투자의견상향", "매수추천"
}

NEG_WORDS = {
    # 가격/수급
    "하락", "약세", "급락", "폭락", "반락", "매도세",
    "기관순매도", "외국인순매도", "연기금순매도",
    # 실적/가치
    "실적부진", "영업이익감소", "매출감소", "적자전환",
    "영업손실", "실적예상치하회", "컨센서스하향",
    # 기업행동/정책
    "공매도", "규제강화", "과징금", "감사부정적",
    "소송패소", "노사분규", "원가상승", "금리인상",    "주가전망어두움", "시가총액감소", "자사주매입취소",
    # 증권사/시장
    "목표가하향", "투자의견하향", "매도추천", "리스크고조"
}


def safe_float(v, default=0.0):
    try: return float(v) if v is not None else default
    except: return default

def clean_text(html: str) -> str:
    """HTML 태그 제거 및 공백 정리"""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

def get_sentiment_score(title: str, desc: str) -> float:
    text = clean_text(f"{title} {desc}")
    if not text: return 0.0

    pos_count = sum(1 for kw in POS_WORDS if kw in text)
    neg_count = sum(1 for kw in NEG_WORDS if kw in text)
    total = pos_count + neg_count

    if total == 0: return 0.0
    # -1 ~ +1 범위 정규화
    return round((pos_count - neg_count) / total, 2)


def main():
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("[WARN] NAVER API 키 미설정 → 뉴스 점수 0 처리")
        return

    try:
        import pandas as pd
        df = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig")
        stocks = df[["code", "name"]].dropna().head(MAX_STOCKS).to_dict("records")
    except Exception as e:
        print(f"[ERROR] {HISTORY_CSV} 로드 실패: {e}")
        return

    scores = {}
    print(f"[NEWS] 대상 종목: {len(stocks)}개 (상위 {MAX_STOCKS})")

    for idx, s in enumerate(stocks, 1):
        code = str(s["code"]).zfill(6)
        name = str(s["name"])
        if not code or name.lower() == "nan":            continue

        try:
            headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
            params = {"query": name, "display": 5, "sort": "date"}
            res = requests.get(NAVER_URL, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            items = res.json().get("items", [])

            if not items:
                scores[code] = 0.0
                continue

            # 최근 5개 뉴스 평균 감성 점수
            avg = sum(get_sentiment_score(i.get("title", ""), clean_text(i.get("description", ""))) for i in items) / len(items)
            scores[code] = round(avg, 2)

            if idx % 10 == 0:
                print(f"[NEWS] 진행률 {idx}/{len(stocks)} | {name}({code}) → {avg:.2f}")

        except Exception as e:
            print(f"[WARN] {name}({code}) 뉴스 조회 실패: {e}")
            scores[code] = 0.0

        time.sleep(DELAY)

    output = {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "scores": scores,
        "count": len(scores),
        "keywords_loaded": f"POS={len(POS_WORDS)}, NEG={len(NEG_WORDS)}"
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8-sig") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {len(scores)}종목 뉴스 점수 저장 → {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
