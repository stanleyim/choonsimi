"""
fetch_news.py — v2.4.0
────────────────────────────────────────────────────────────
v2.3.1 대비 변경:
  ✔ history.csv 최신날짜 필터 + 코드 중복 제거 (핵심 수정)
  ✔ csv.DictReader → pandas (다중날짜 누적 CSV 대응)
────────────────────────────────────────────────────────────
"""

import os, json, time, re, requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

NAVER_URL   = "https://openapi.naver.com/v1/search/news.json"
OUTPUT_PATH = "news_scores.json"
INPUT_CSV   = "history.csv"
KST         = timezone(timedelta(hours=9))

MAX_STOCKS  = 200
WORKERS     = 3
SLEEP_SEC   = 0.25
MAX_RETRIES = 3

CLEAN_HTML  = re.compile(r"<[^>]+>")

POS_KEYWORDS = [
    "상한가","급등","강세","매수","호재","신기록",
    "역대최고","성장","확대","수주","흑자","배당","인수"
]

NEG_KEYWORDS = [
    "하한가","급락","약세","매도","악재","경고",
    "감자","영업정지","부실","적자","소송","부도","연체"
]


def calc_sentiment(items):
    if not items: return 0.0
    pos = neg = 0
    for item in items:
        title = CLEAN_HTML.sub("", item.get("title",      "")).lower()
        desc  = CLEAN_HTML.sub("", item.get("description","")).lower()
        pos += sum(2 for k in POS_KEYWORDS if k in title)
        pos += sum(1 for k in POS_KEYWORDS if k in desc)
        neg += sum(2 for k in NEG_KEYWORDS if k in title)
        neg += sum(1 for k in NEG_KEYWORDS if k in desc)
    total = pos + neg
    return (pos - neg) / total if total > 0 else 0.0


def fetch_news_for_stock(args):
    code, name, cid, csec = args
    query = f"{name} OR {code}"
    hdrs  = {
        "X-Naver-Client-Id":     cid,
        "X-Naver-Client-Secret": csec
    }
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.get(
                NAVER_URL,
                params={"query": query, "display": 10, "sort": "date"},
                headers=hdrs,
                timeout=(3, 10)
            )
            if res.status_code == 429:
                time.sleep(min(2 ** (attempt + 1), 10))
                continue
            res.raise_for_status()
            score = calc_sentiment(res.json().get("items", []))
            time.sleep(SLEEP_SEC)
            return code, round(score, 2)
        except requests.exceptions.Timeout:
            time.sleep(0.5)
        except requests.exceptions.RequestException:
            time.sleep(1)
        except Exception:
            time.sleep(0.5)
    return code, 0.0


def run():
    print("[NEWS START]")

    cid  = os.environ.get("NAVER_CLIENT_ID",     "")
    csec = os.environ.get("NAVER_CLIENT_SECRET", "")

    if not cid or not csec:
        print("⚠️ NAVER API 키 없음 → 중립 처리")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # ✅ 최신날짜 필터 + 코드 중복 제거
    try:
        df = pd.read_csv(INPUT_CSV, dtype={"code": str}, encoding="utf-8-sig")
        df["code"]   = df["code"].str.zfill(6)
        latest_date  = df["date"].max()
        df_latest    = df[df["date"] == latest_date].drop_duplicates("code")
        target       = df_latest[["code","name"]].to_dict("records")[:MAX_STOCKS]
        print(f"[DATA] 기준일={latest_date} | 대상={len(target)}종목")
    except Exception as e:
        print(f"⚠️ {INPUT_CSV} 로드 실패: {e}")
        return

    if not target:
        print("⚠️ 대상 종목 없음 → 스킵")
        return

    args_list = [(r["code"], r["name"], cid, csec) for r in target]
    print(f"🎯 {len(args_list)}종목 | Title(2x)/Desc(1x) 가중치 적용")

    scores = {}
    done   = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(fetch_news_for_stock, a) for a in args_list]
        for f in as_completed(futures):
            done += 1
            code, score = f.result()
            scores[code] = score
            if done % 20 == 0:
                print(f"⏳ {done}/{len(args_list)} 처리중...")

    output = {
        "date":   today,
        "scores": scores,
        "count":  len(scores)
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8-sig") as fp:
        json.dump(output, fp, ensure_ascii=False, indent=2)

    print(f"[NEWS DONE] {len(scores)}종목 저장 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
