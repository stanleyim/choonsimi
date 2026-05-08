"""
fetch_news.py — v1.2 (Option A Patch + Names Added) — FIXED
────────────────────────────────────────────────────
✔ Syntax Error FIXED
✔ None / NaN SAFE
✔ HTML CLEANING 유지
✔ API 안정성 유지
✔ names mapping 포함
✔ engine.py 호환 유지
────────────────────────────────────────────────────
"""

import os, json, requests, time, re
from datetime import datetime, timezone, timedelta

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
HISTORY_CSV = "history.csv"
OUTPUT_JSON = "news_scores.json"
KST = timezone(timedelta(hours=9))

MAX_STOCKS = 50
DELAY = 0.3

# ─────────────────────────────────────────────
POS_WORDS = {
    "상승","강세","급등","반등","회복","매수세",
    "기관순매수","외국인순매수","연기금순매수",
    "실적호조","영업이익증가","매출증가","흑자전환",
    "실적예상치상회","컨센서스상향","배당성향상승",
    "자사주매입","기술수출","수주성공","신사업성공",
    "규제완화","금리인하","주가전망밝음","시가총액증가",
    "목표가상향","투자의견상향","매수추천"
}

NEG_WORDS = {
    "하락","약세","급락","폭락","반락","매도세",
    "기관순매도","외국인순매도","연기금순매도",
    "실적부진","영업이익감소","매출감소","적자전환",
    "영업손실","실적예상치하회","컨센서스하향",
    "공매도","규제강화","과징금","감사부정적",
    "소송패소","노사분규","원가상승","금리인상",
    "주가전망어두움","시가총액감소","자사주매입취소",
    "목표가하향","투자의견하향","매도추천","리스크고조"
}


def clean_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(html))
    return re.sub(r"\s+", " ", text).strip()


def get_sentiment_score(title: str, desc: str) -> float:
    text = clean_text(f"{title} {desc}")
    if not text:
        return 0.0

    pos = sum(1 for k in POS_WORDS if k in text)
    neg = sum(1 for k in NEG_WORDS if k in text)
    total = pos + neg

    if total == 0:
        return 0.0

    return round((pos - neg) / total, 2)


def main():
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[WARN] NAVER API KEY missing → skip news scoring")
        return

    try:
        import pandas as pd
        df = pd.read_csv(HISTORY_CSV, encoding="utf-8-sig")
        stocks = df[["code", "name"]].dropna().head(MAX_STOCKS).to_dict("records")
    except Exception as e:
        print(f"[ERROR] history.csv load failed: {e}")
        return

    scores = {}
    names = {}

    print(f"[NEWS] processing {len(stocks)} stocks")

    for idx, s in enumerate(stocks, 1):
        code = str(s.get("code", "")).zfill(6)
        name = str(s.get("name", ""))

        if not code or str(name).lower() == "nan":
            continue

        names[code] = name

        try:
            headers = {
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret
            }

            params = {
                "query": name,
                "display": 5,
                "sort": "date"
            }

            res = requests.get(NAVER_URL, headers=headers, params=params, timeout=10)
            res.raise_for_status()

            items = res.json().get("items", [])

            if not items:
                scores[code] = 0.0
                continue

            avg = sum(
                get_sentiment_score(
                    i.get("title", ""),
                    clean_text(i.get("description", ""))
                )
                for i in items
            ) / len(items)

            scores[code] = round(avg, 2)

            if idx % 10 == 0:
                print(f"[NEWS] {idx}/{len(stocks)} {name}({code}) → {avg:.2f}")

        except Exception as e:
            print(f"[WARN] {name}({code}) failed: {e}")
            scores[code] = 0.0

        time.sleep(DELAY)

    output = {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "scores": scores,
        "names": names,
        "count": len(scores),
        "keywords_loaded": f"POS={len(POS_WORDS)}, NEG={len(NEG_WORDS)}"
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8-sig") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[DONE] saved {len(scores)} stocks → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
