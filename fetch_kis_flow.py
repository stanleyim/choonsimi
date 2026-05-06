"""
fetch_kis_flow.py  v4
────────────────────────────────────────────────────────────
v3 대비 개선:
  1. [FIX] 500 에러 해결: FID_COND_MRKT_DIV_CODE="V" 고정
  2. [NEW] confidence 기반 신호 품질 계량화
  3. [NEW] 빈 응답 → 점수 유지 + 신뢰도 하락 (0.3)
  4. [NEW] 반복 에러 감지 → degraded_mode 전환
  5. [NEW] engine 호환 구조: {score, confidence, status, rows}
────────────────────────────────────────────────────────────
"""

import os
import sys
import json
import math
import time
import requests
from datetime import datetime

KIS_BASE     = "https://openapi.koreainvestment.com:9443"
TIMEOUT      = 10
DELAY        = 0.3
PERCENTILE   = 0.80
HISTORY_MAX  = 20
FLOW_FILE    = "market_flow.json"

_last_known = {}
_error_counts = {}
ERROR_THRESHOLD = 3


def get_token() -> str:
    res = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"],
        },
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def parse_net(value) -> int:
    try:
        v = int(str(value).replace(",", "").strip())
        return int(math.copysign(math.log1p(abs(v)), v))
    except (ValueError, TypeError):
        return 0


def dynamic_filter(rows: list) -> list:
    if not rows:
        return []
    thresholds = sorted(abs(r["net"]) for r in rows)
    cutoff = thresholds[int(len(thresholds) * (1 - PERCENTILE))]
    return [r for r in rows if abs(r["net"]) >= cutoff]


def compute_score(rows: list) -> float:
    if not rows:
        return 0.0
    avg = sum(r["net"] for r in rows) / len(rows)
    return round(math.tanh(avg / 1_000_000), 4)


def _get_key(market: str, investor: str) -> str:
    return f"{market}_{investor}"


def _update_state(key: str, score: float, confidence: float):
    _last_known[key] = {"score": score, "confidence": confidence}
    _error_counts[key] = 0


def _handle_no_data(key: str) -> dict:
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {
        "score": prev["score"],
        "confidence": 0.3,
        "status": "no_data",
        "rows": []
    }


def _handle_degraded(key: str) -> dict:
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {
        "score": prev["score"],
        "confidence": 0.2,
        "status": "degraded_mode",
        "rows": []
    }


def load_history() -> dict:
    try:
        with open(FLOW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def append_history(existing: dict, today: dict) -> dict:
    history = existing.get("history", [])
    today_date = today["date"]

    history = [h for h in history if h.get("date") != today_date]
    history.append({
        "date": today_date,
        "scores": {k: v["score"] for k, v in today.items() if isinstance(v, dict) and "score" in v},
    })

    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]

    today["history"] = history
    return today


def fetch_one(token: str, market: str, investor: str) -> dict:
    key = _get_key(market, investor)

    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"

    iscd = "0001" if market == "J" else "1001"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": os.environ["KIS_APP_KEY"],
        "appsecret": os.environ["KIS_APP_SECRET"],
        "tr_id": "FHPTJ04400000",
        "custtype": "P",
    }

    def build_params(div_cls: str):
        return {
            "FID_COND_MRKT_DIV_CODE": "V",
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": iscd,
            "FID_DIV_CLS_CODE": div_cls,
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE": investor,
        }

    def call(params):
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    key_state = _get_key(market, investor)

    try:
        data = call(build_params("1"))

        if data.get("rt_cd") == "0" and not data.get("output"):
            data = call(build_params("0"))

        if data.get("rt_cd") != "0":
            _error_counts[key_state] = _error_counts.get(key_state, 0) + 1
            if _error_counts[key_state] >= ERROR_THRESHOLD:
                return _handle_degraded(key_state)
            return _handle_no_data(key_state)

        rows = []
        for item in data.get("output", []):
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            net = parse_net(item.get(net_key, 0))
            rows.append({"code": code, "net": net})

        rows = dynamic_filter(rows)
        score = compute_score(rows)

        confidence = 0.95
        _update_state(key_state, score, confidence)

        return {
            "score": score,
            "confidence": confidence,
            "status": "normal",
            "rows": rows
        }

    except Exception:
        _error_counts[key_state] = _error_counts.get(key_state, 0) + 1
        if _error_counts[key_state] >= ERROR_THRESHOLD:
            return _handle_degraded(key_state)
        return _handle_no_data(key_state)


if __name__ == "__main__":
    today_str = datetime.now().strftime("%Y-%m-%d")

    token = get_token()

    keys = [
        ("J", "1", "KOSPI_foreign"),
        ("J", "2", "KOSPI_institution"),
        ("Q", "1", "KOSDAQ_foreign"),
        ("Q", "2", "KOSDAQ_institution"),
    ]

    snapshots = {}

    for market, investor, key in keys:
        snapshots[key] = fetch_one(token, market, investor)
        time.sleep(DELAY)

    today_snapshot = {
        "date": today_str,
        "scores": {k: v["score"] for k, v in snapshots.items()},
        "confidences": {k: v["confidence"] for k, v in snapshots.items()},
        "statuses": {k: v["status"] for k, v in snapshots.items()},
        **snapshots,
        "updated_at": datetime.now().isoformat(),
    }

    existing = load_history()
    result = append_history(existing, today_snapshot)

    with open(FLOW_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {FLOW_FILE} 저장 완료")
