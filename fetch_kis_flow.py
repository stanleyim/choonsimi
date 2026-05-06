"""
fetch_kis_flow.py  v3
────────────────────────────────────────────────────────────
v2 대비 개선:
  1. score/net 이중 의미 제거 → log-sum 단일 파이프라인
  2. TOP_N dynamic → abs(net) 상위 80% percentile 필터
  3. market_flow 시계열 구조 → history 누적 (최대 20일)
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
PERCENTILE   = 0.80   # 상위 80% 필터 기준
HISTORY_MAX  = 20     # 시계열 최대 보관 일수
FLOW_FILE    = "market_flow.json"


# ──────────────────────────────────────────────
# 토큰
# ──────────────────────────────────────────────
def get_token() -> str:
    res = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey":     os.environ["KIS_APP_KEY"],
            "appsecret":  os.environ["KIS_APP_SECRET"],
        },
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    return res.json()["access_token"]


# ──────────────────────────────────────────────
# log-scale net (방향 보존 + 대형주 bias 완화)
# ──────────────────────────────────────────────
def parse_net(value) -> int:
    try:
        v = int(str(value).replace(",", "").strip())
        return int(math.copysign(math.log1p(abs(v)), v))
    except (ValueError, TypeError):
        return 0


# ──────────────────────────────────────────────
# dynamic TOP_N: abs(net) 상위 80% 만 사용
# ──────────────────────────────────────────────
def dynamic_filter(rows: list) -> list:
    """
    전체 rows 중 abs(net) 기준 상위 PERCENTILE만 추출.
    large-cap 장세 / small-cap 장세 자동 적응.
    """
    if not rows:
        return []
    threshold = sorted(abs(r["net"]) for r in rows)
    cutoff = threshold[int(len(threshold) * (1 - PERCENTILE))]
    filtered = [r for r in rows if abs(r["net"]) >= cutoff]
    print(f"[KIS] dynamic filter → 전체={len(rows)} / 통과={len(filtered)} (cutoff={cutoff})")
    return filtered


# ──────────────────────────────────────────────
# score: log-sum → tanh 단일 파이프라인
# (log-scale net 합산 후 정규화 — 이중 변환 없음)
# ──────────────────────────────────────────────
def compute_score(rows: list) -> float:
    if not rows:
        return 0.0
    # 평균 기반 → 종목 수 차이로 인한 왜곡 제거
    avg = sum(r["net"] for r in rows) / len(rows)
    return round(math.tanh(avg / 1_000_000), 4)


# ──────────────────────────────────────────────
# 시계열 로드 / 저장
# ──────────────────────────────────────────────
def load_history() -> dict:
    try:
        with open(FLOW_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def append_history(existing: dict, today: dict) -> dict:
    """
    history 키에 오늘 snapshot 추가, HISTORY_MAX 초과 시 오래된 것 제거.
    """
    history = existing.get("history", [])
    today_date = today["date"]

    # 같은 날짜 중복 방지
    history = [h for h in history if h.get("date") != today_date]
    history.append({
        "date":  today_date,
        "scores": today["scores"],
    })

    # 최대 보관 일수 유지
    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]

    today["history"] = history
    return today


# ──────────────────────────────────────────────
# KIS API 호출
# ──────────────────────────────────────────────
def fetch_one(token: str, market: str, investor: str) -> dict:
    """
    반환:
    {
        "rows":  [{"code": "005930", "net": 15}, ...],  ← log-scale
        "score": float (-1 ~ 1)
    }
    """
    url     = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd    = "0001" if market == "J" else "1001"
    label   = "외국인" if investor == "1" else "기관"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ.get("KIS_APP_KEY", ""),
        "appsecret":     os.environ.get("KIS_APP_SECRET", ""),
        "tr_id":         "FHPTJ04400000",
        "custtype":      "P",
    }

    def build_params(div_cls: str) -> dict:
        return {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_COND_SCR_DIV_CODE":  "16449",
            "FID_INPUT_ISCD":         iscd,
            "FID_DIV_CLS_CODE":       div_cls,
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE":       investor,
        }

    def call(params):
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    EMPTY = {"rows": [], "score": 0.0}

    try:
        print(f"[KIS] 요청 → {market}/{label}")
        data = call(build_params("1"))
        rt   = data.get("rt_cd")
        print(f"[KIS] rt={rt} / msg={data.get('msg1')}")

        if rt == "0" and not data.get("output"):
            print(f"[KIS] 0건 → fallback")
            data = call(build_params("0"))

        if data.get("rt_cd") != "0":
            print(f"[KIS] ❌ {data.get('msg1')}")
            return EMPTY

        output = data.get("output", [])
        if not isinstance(output, list):
            return EMPTY

        # 전체 파싱 후 dynamic filter
        all_rows = []
        for item in output:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            if not code or code == "000000":
                continue
            net = parse_net(item.get(net_key, 0))
            all_rows.append({"code": code, "net": net})

        rows  = dynamic_filter(all_rows)
        score = compute_score(rows)
        print(f"[KIS] ✅ {market}/{label} → score={score}")
        return {"rows": rows, "score": score}

    except requests.exceptions.Timeout:
        print(f"[KIS] ⏱ TIMEOUT {market}/{label}")
        return EMPTY
    except requests.exceptions.HTTPError as e:
        print(f"[KIS] 🔴 HTTP {e}")
        return EMPTY
    except Exception as e:
        print(f"[KIS] 💥 {type(e).__name__}: {e}")
        return EMPTY


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
if __name__ == "__main__":
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[START] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        token = get_token()
        print("[KIS] ✅ 토큰 발급 완료")
    except Exception as e:
        print(f"[KIS] 토큰 발급 실패: {e}")
        sys.exit(1)

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

    # 오늘 scores 요약 (시계열용)
    today_snapshot = {
        "date":  today_str,
        "scores": {k: snapshots[k]["score"] for k in snapshots},
        **snapshots,   # rows + score 전체 포함
        "updated_at": datetime.now().isoformat(),
    }

    # 기존 history 불러와서 append
    existing = load_history()
    result   = append_history(existing, today_snapshot)

    with open(FLOW_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] ✅ {FLOW_FILE} 저장 완료 (history={len(result.get('history', []))}일)")
