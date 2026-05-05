"""
fetch_kis_flow.py — v2.0
- FID_COND_MRKT_DIV_CODE 파라미터 수정 (핵심 버그 수정)
- fail-fast: 실패 시 즉시 종료 (10분 낭비 제거)
- retry 2회
- graceful degradation: 실패해도 빈 파일 생성
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

KIS_BASE    = "https://openapi.koreainvestment.com:9443"
OUTPUT_PATH = "stock_flow.json"
TOP_N       = 100
KST         = timezone(timedelta(hours=9))
TIMEOUT     = 10   # fail-fast: 10초
MAX_RETRY   = 2


def get_access_token() -> str:
    url     = f"{KIS_BASE}/oauth2/tokenP"
    headers = {"Content-Type": "application/json"}
    body    = {
        "grant_type": "client_credentials",
        "appkey":     os.environ["KIS_APP_KEY"],
        "appsecret":  os.environ["KIS_APP_SECRET"],
    }
    res = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
    res.raise_for_status()
    token = res.json().get("access_token", "")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {res.text}")
    print("[KIS] 토큰 발급 완료")
    return token


def fetch_investor_flow(token: str, market: str, investor: str) -> list:
    """
    국내기관_외국인 매매종목가집계
    ✅ 수정: FID_COND_MRKT_DIV_CODE 명시적으로 포함
    market:   J=KOSPI / Q=KOSDAQ
    investor: 1=외국인 / 2=기관
    """
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    today = datetime.now(KST).strftime("%Y%m%d")

    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "tr_id":         "FHPTJ04400000",
        "custtype":      "P",
    }

    # ✅ 핵심 수정: FID_COND_MRKT_DIV_CODE 명시
    params = {
        "FID_COND_MRKT_DIV_CODE": market,        # ✅ J or Q 명시
        "FID_INPUT_DATE_1":        today,
        "FID_INPUT_DATE_2":        today,
        "FID_RANK_SORT_CLS_CODE":  "0",           # 순매수 상위
        "FID_ETC_CLS_CODE":        investor,      # 1=외국인 / 2=기관
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            res = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=TIMEOUT
            )
            res.raise_for_status()
            data = res.json()

            if data.get("rt_cd") != "0":
                msg = data.get("msg1", "")
                print(f"[KIS] {market}/{'외국인' if investor=='1' else '기관'} 오류: {msg}")
                return []

            rows = []
            for item in data.get("output", [])[:TOP_N]:
                code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
                # 외국인: frgn_ntby_qty / 기관: orgn_ntby_qty
                if investor == "1":
                    net = int(item.get("frgn_ntby_qty", 0) or 0)
                else:
                    net = int(item.get("orgn_ntby_qty", 0) or 0)
                if code and code != "000000":
                    rows.append({"code": code, "net": net})

            label = "외국인" if investor == "1" else "기관"
            print(f"[KIS] {market}/{label} → {len(rows)}종목")
            return rows

        except requests.exceptions.Timeout:
            print(f"[KIS] {market}/{investor} timeout (시도 {attempt}/{MAX_RETRY})")
            if attempt == MAX_RETRY:
                return []
            time.sleep(1)

        except Exception as e:
            print(f"[KIS] {market}/{investor} 실패: {e}")
            return []

    return []


def save_empty():
    """실패 시 빈 파일 생성 (graceful degradation)"""
    if not os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)
    print("[KIS] 빈 stock_flow.json 유지")


def run():
    print("[KIS FLOW START]")

    if not os.environ.get("KIS_APP_KEY") or not os.environ.get("KIS_APP_SECRET"):
        print("[KIS] KEY 없음 → skip")
        save_empty()
        return

    try:
        token = get_access_token()
    except Exception as e:
        print(f"[KIS] 토큰 실패 → skip: {e}")
        save_empty()
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 수집 (fail-fast 적용)
    foreign_k = fetch_investor_flow(token, "J", "1"); time.sleep(0.3)
    foreign_q = fetch_investor_flow(token, "Q", "1"); time.sleep(0.3)
    inst_k    = fetch_investor_flow(token, "J", "2"); time.sleep(0.3)
    inst_q    = fetch_investor_flow(token, "Q", "2")

    # 종목별 합산
    flow_map = {}
    for item in foreign_k + foreign_q:
        code = item["code"]
        flow_map.setdefault(code, {"code": code, "foreign_net": 0, "inst_net": 0, "date": today})
        flow_map[code]["foreign_net"] += item["net"]

    for item in inst_k + inst_q:
        code = item["code"]
        flow_map.setdefault(code, {"code": code, "foreign_net": 0, "inst_net": 0, "date": today})
        flow_map[code]["inst_net"] += item["net"]

    result = list(flow_map.values())

    if not result:
        print("[KIS] 수집 결과 없음 → 기존 파일 유지")
        save_empty()
        return

    # 기존 파일 누적 (오늘 중복 제거)
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                old = json.load(f)
            old = [r for r in old if r.get("date") != today]
            result = old + result
        except Exception as e:
            print(f"[KIS] 기존 파일 로드 실패 (신규): {e}")

    # 최근 30일 유지
    result = sorted(result, key=lambda x: x.get("date", ""), reverse=True)
    result = result[:TOP_N * 30]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[KIS FLOW DONE] {len(flow_map)}종목 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
