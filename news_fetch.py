"""
news_fetch.py — v4.0
- Fix #1: code zfill(6) 정규화 추가 (merge 실패 방지)
- Fix #2: 거래량 상위 50종목 동적 뉴스 수집 추가 (커버리지 확대)
- 기존 로직 유지
"""

import os
import json
import time

BASE = "https://news.google.com/rss/search?q="

# ── 섹터 키워드 ──────────────────────────────────────
KEYWORDS = [
    "반도체", "AI인공지능", "2차전지", "전기차",
    "바이오", "원전", "방산", "조선",
    "로봇", "자율주행", "신재생에너지", "수소",
    "금리", "환율", "CPI", "FOMC",
    "코스피", "외국인매수", "기관매수",
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

# ── 폴백 종목 코드 ─────────────────────────────────
FALLBACK_CODE_MAP = {
    "삼성전자": "005930", "SK하이닉스": "000660", "현대차": "005380",
    "기아": "000270", "LG에너지솔루션": "373220", "삼성바이오로직스": "207940",
    "셀트리온": "068270", "NAVER": "035420", "카카오": "035720",
    "포스코": "005490", "LG화학": "051910", "삼성SDI": "006400",
    "현대모비스": "012330", "KB금융": "105560", "신한지주": "055550",
    "하나금융": "086790", "LG전자": "066570", "한화에어로스페이스": "012450",
    "HD현대중공업": "329180", "두산에너빌리티": "034020",
    "에코프로": "086520", "에코프로비엠": "247540",
    "포스코퓨처엠": "003670", "한화오션": "042660",
    "SK이노베이션": "096770", "고려아연": "010130",
    "HMM": "011200", "대한항공": "003490",
    "한미약품": "128940", "크래프톤": "259960",
}

TOP_VOLUME_N = 50  # 거래량 상위 N종목 동적 수집


def _normalize_code(code: str) -> str:
    """✅ Fix #1: code 6자리 정규화"""
    return (
        str(code)
        .replace(".0", "")
        .strip()
        .zfill(6)
    )


def load_code_map() -> dict:
    """data.json → 종목명-코드 매핑 (code zfill 적용)"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_path = os.path.join(root, "data.json")

        if not os.path.exists(data_path):
            return {k: _normalize_code(v) for k, v in FALLBACK_CODE_MAP.items()}

        with open(data_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        code_map = {}
        for item in raw.get("all", []):
            name = item.get("name", "").strip()
            code = item.get("code", "").strip()
            if name and code:
                code_map[name] = _normalize_code(code)  # ✅ zfill 적용

        if len(code_map) < 10:
            return {k: _normalize_code(v) for k, v in FALLBACK_CODE_MAP.items()}

        return code_map

    except Exception as e:
        print(f"[NEWS] code_map 로드 실패 → fallback: {e}")
        return {k: _normalize_code(v) for k, v in FALLBACK_CODE_MAP.items()}


def load_top_volume_names(n: int = TOP_VOLUME_N) -> list:
    """
    ✅ Fix #2: data.json 거래량 상위 N종목 이름 반환
    → KEYWORDS에 없는 거래량 상위 종목도 뉴스 수집
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_path = os.path.join(root, "data.json")

        if not os.path.exists(data_path):
            return []

        with open(data_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        items = raw.get("all", [])
        # volume 기준 정렬
        items_sorted = sorted(
            items,
            key=lambda x: int(x.get("volume", 0)),
            reverse=True
        )

        names = []
        for item in items_sorted[:n]:
            name = item.get("name", "").strip()
            if name:
                names.append(name)

        return names

    except Exception as e:
        print(f"[NEWS] top_volume 로드 실패: {e}")
        return []


def fetch_titles(keyword: str) -> list:
    """Google RSS 뉴스 수집"""
    try:
        import feedparser
        url = f"{BASE}{keyword}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        return [getattr(e, "title", "") or "" for e in feed.entries[:15]]
    except ImportError:
        print("[NEWS] feedparser 미설치")
        return []
    except Exception as e:
        print(f"[NEWS] {keyword} 실패: {e}")
        return []


def score_title(title: str) -> float:
    """감성 점수"""
    s = 0
    for p in POS:
        if p in title:
            s += 1
    for n in NEG:
        if n in title:
            s -= 1
    return float(s)


def map_code(title: str, code_map: dict):
    """종목명 → 코드"""
    for name, code in code_map.items():
        if name in title:
            return code
    return None


def run() -> list:
    print("[NEWS START]")

    code_map = load_code_map()
    rows = []

    # 1) 섹터 키워드 기반 수집 (기존)
    for kw in KEYWORDS:
        for title in fetch_titles(kw):
            if not title:
                continue
            code = map_code(title, code_map)
            if not code:
                continue
            rows.append({"code": code, "score": score_title(title)})
        time.sleep(0.2)

    # 2) ✅ Fix #2: 거래량 상위 종목 직접 수집 (커버리지 확대)
    top_names = load_top_volume_names(TOP_VOLUME_N)
    fetched_names = set()

    for name in top_names:
        if name in fetched_names:
            continue
        # 이미 KEYWORDS에 있는 종목은 skip (중복 방지)
        if name in KEYWORDS:
            continue

        code = code_map.get(name)
        if not code:
            continue

        for title in fetch_titles(name):
            if not title:
                continue
            rows.append({"code": code, "score": score_title(title)})

        fetched_names.add(name)
        time.sleep(0.2)

    if not rows:
        print("[NEWS] empty")
        return []

    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        # code 정규화 한 번 더 (안전장치)
        df["code"] = df["code"].astype(str).str.zfill(6)
        out = df.groupby("code", as_index=False)["score"].sum()
        # score=0 제거 (의미 없는 종목 제외)
        out = out[out["score"] != 0].reset_index(drop=True)
        print(f"[NEWS DONE] {len(out)}종목")
        return out.to_dict("records")

    except Exception as e:
        print(f"[NEWS] pandas error: {e}")
        return []


if __name__ == "__main__":
    result = run()
    for r in result:
        print(r)
