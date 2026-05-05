"""
fetch_kis_flow.py — v1.0
KIS API → 종목별 외국인/기관 순매수 수집 → stock_flow.json
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


def get_access_token() -> str:
    url     = f"{KIS_BASE}/oauth2/tokenP"
    headers = {"Content-Type": "application/json"}
    body    = {
        "grant_type": "client_credentials",
        "appkey":     os.environ["KIS_APP_KEY"],
        "appsecret":  os.environ["KIS_APP_SECRET"],
    }
    res = requests.post(url, headers=headers, json=body, timeout=10)
    res.raise_for_status()
    token = res.json().get("access_token", "")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {res.text}")
    print("[KIS] 토큰 발급 완료")
    return token


def fetch_investor_flow(token: str, market: str, investor: str) -> list:
    """
    국내기관_외국인 매매종목가집계
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
    params = {
        "INQR_STRT_DT":       today,
        "INQR_END_DT":        today,
        "SYDY_LWPR_YN":       "N",
        "TRHT_YN":            "N",
        "EXBL_WHOS_CLS_CODE": investor,
        "TRDE_CLS_CODE":      "0",
        "MRKT_CLS_CODE":      market,
        "CTX_AREA_FK100":     "",
        "CTX_AREA_NK100":     "",
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            print(f"[KIS] {market}/{investor} 오류: {data.get('msg1')}")
            return []

        rows = []
        for item in data.get("output", [])[:TOP_N]:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            if investor == "1":
                net = int(item.get("frgn_ntby_qty", 0) or 0)
            else:
                net = int(item.get("orgn_ntby_qty", 0) or 0)
            if code and code != "000000":
                rows.append({"code": code, "net": net})

        label = "외국인" if investor == "1" else "기관"
        print(f"[KIS] {market}/{label} → {len(rows)}종목")
        return rows

    except Exception as e:
        print(f"[KIS] {market}/{investor} 실패: {e}")
        return []


def run():
    print("[KIS FLOW START]")

    if not os.environ.get("KIS_APP_KEY") or not os.environ.get("KIS_APP_SECRET"):
        print("[KIS] KEY 없음 → skip")
        return

    try:
        token = get_access_token()
    except Exception as e:
        print(f"[KIS] 토큰 실패 → skip: {e}")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 수집
    foreign_k = fetch_investor_flow(token, "J", "1"); time.sleep(0.5)
    foreign_q = fetch_investor_flow(token, "Q", "1"); time.sleep(0.5)
    inst_k    = fetch_investor_flow(token, "J", "2"); time.sleep(0.5)
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

    # 기존 파일 누적 (오늘 날짜 중복 제거)
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
