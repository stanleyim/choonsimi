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
PERCENTILE   = 0.80   # 상위 80% 필터 기준
HISTORY_MAX  = 20     # 시계열 최대 보관 일수
FLOW_FILE    = "market_flow.json"

# ──────────────────────────────────────────────
# 글로벌 상태: 신뢰도 기반 설계를 위한 캐시
# ──────────────────────────────────────────────
_last_known = {}      # {key: {"score": float, "confidence": float}}
_error_counts = {}    # {key: int}  # 연속 에러 카운트
ERROR_THRESHOLD = 3   # degraded_mode 전환 임계값


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
    thresholds = sorted(abs(r["net"]) for r in rows)
    cutoff = thresholds[int(len(thresholds) * (1 - PERCENTILE))]
    filtered = [r for r in rows if abs(r["net"]) >= cutoff]
    print(f"[KIS] dynamic filter → 전체={len(rows)} / 통과={len(filtered)} (cutoff={cutoff})")
    return filtered


# ──────────────────────────────────────────────
# score: log-sum → tanh 단일 파이프라인
# ──────────────────────────────────────────────
def compute_score(rows: list) -> float:
    if not rows:
        return 0.0
    # 평균 기반 → 종목 수 차이로 인한 왜곡 제거
    avg = sum(r["net"] for r in rows) / len(rows)
    return round(math.tanh(avg / 1_000_000), 4)


# ──────────────────────────────────────────────
# 신뢰도 계산 유틸
# ──────────────────────────────────────────────
def _get_key(market: str, investor: str) -> str:
    return f"{market}_{investor}"


def _update_state(key: str, score: float, confidence: float):
    """성공 시 상태 업데이트"""    _last_known[key] = {"score": score, "confidence": confidence}
    _error_counts[key] = 0  # 에러 카운트 리셋


def _handle_no_data(key: str) -> dict:
    """데이터 없음: 이전 점수 유지 + 신뢰도 하락"""
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {
        "score": prev["score"],
        "confidence": 0.3,
        "status": "no_data",
        "rows": []
    }


def _handle_degraded(key: str) -> dict:
    """반복 에러: 신뢰도 추가 하락 + degraded 모드"""
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {
        "score": prev["score"],
        "confidence": 0.2,  # 추가 페널티
        "status": "degraded_mode",
        "rows": []
    }


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
        "scores": {k: v["score"] for k, v in today.items() if isinstance(v, dict) and "score" in v},    })

    # 최대 보관 일수 유지
    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]

    today["history"] = history
    return today


# ──────────────────────────────────────────────
# KIS API 호출 (v4: confidence 기반)
# ──────────────────────────────────────────────
def fetch_one(token: str, market: str, investor: str) -> dict:
    """
    반환:
    {
        "score": float,           # -1 ~ 1
        "confidence": float,      # 0.2 ~ 1.0 (신호 품질)
        "status": str,            # "normal" / "no_data" / "degraded_mode"
        "rows": [{"code": str, "net": int}, ...]
    }
    """
    key = _get_key(market, investor)
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd = "0001" if market == "J" else "1001"
    label = "외국인" if investor == "1" else "기관"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    # 🔑 핵심 수정: 시장구분코드는 항상 "V" (주식)
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ.get("KIS_APP_KEY", ""),
        "appsecret":     os.environ.get("KIS_APP_SECRET", ""),
        "tr_id":         "FHPTJ04400000",  # 공식 문서 기준 (확인 필요)
        "custtype":      "P",
    }

    def build_params(div_cls: str) -> dict:
        return {
            "FID_COND_MRKT_DIV_CODE": "V",  # ✅ 항상 "V" (주식)
            "FID_COND_SCR_DIV_CODE":  "16449",
            "FID_INPUT_ISCD":         iscd,  # ✅ 시장 구분은 여기로: 0001/1001
            "FID_DIV_CLS_CODE":       div_cls,
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE":       investor,
        }

    def call(params):        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    EMPTY_NORMAL = {"score": 0.5, "confidence": 0.3, "status": "no_data", "rows": []}

    try:
        print(f"[KIS] 요청 → {market}/{label}")
        data = call(build_params("1"))
        rt = data.get("rt_cd")
        print(f"[KIS] rt={rt} / msg={data.get('msg1')}")

        # 0건 → fallback 시도
        if rt == "0" and not data.get("output"):
            print(f"[KIS] 0건 → fallback (div_cls=0)")
            data = call(build_params("0"))

        # API 에러 처리
        if data.get("rt_cd") != "0":
            print(f"[KIS] ❌ {data.get('msg1')}")
            _error_counts[key] = _error_counts.get(key, 0) + 1
            if _error_counts[key] >= ERROR_THRESHOLD:
                return _handle_degraded(key)
            return _handle_no_data(key)

        output = data.get("output", [])
        if not isinstance(output, list):
            return _handle_no_data(key)

        # 데이터 파싱
        all_rows = []
        for item in output:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            if not code or code == "000000":
                continue
            net = parse_net(item.get(net_key, 0))
            all_rows.append({"code": code, "net": net})

        # dynamic filter + score 계산
        rows = dynamic_filter(all_rows)
        score = compute_score(rows)

        # ✅ 정상 케이스: 신뢰도 0.95, 상태 업데이트
        confidence = 0.95
        _update_state(key, score, confidence)
        print(f"[KIS] ✅ {market}/{label} → score={score:.4f} / conf={confidence}")

        return {
            "score": score,
            "confidence": confidence,            "status": "normal",
            "rows": rows
        }

    except requests.exceptions.Timeout:
        print(f"[KIS] ⏱ TIMEOUT {market}/{label}")
        _error_counts[key] = _error_counts.get(key, 0) + 1
        return _handle_degraded(key) if _error_counts[key] >= ERROR_THRESHOLD else _handle_no_data(key)

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if hasattr(e, 'response') else None
        print(f"[KIS] 🔴 HTTP {status_code} {e}")
        
        # 500 에러는 파라미터/서버 문제 → 패턴 감지
        if status_code == 500:
            _error_counts[key] = _error_counts.get(key, 0) + 1
            if _error_counts[key] >= ERROR_THRESHOLD:
                print(f"[KIS] ⚠️ {key} degraded_mode activated")
                return _handle_degraded(key)
        return _handle_no_data(key)

    except Exception as e:
        print(f"[KIS] 💥 {type(e).__name__}: {e}")
        return _handle_no_data(key)


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
        snapshots[key] = fetch_one(token, market, investor)        time.sleep(DELAY)

    # 오늘 scores 요약 (시계열용)
    today_snapshot = {
        "date":  today_str,
        "scores": {k: snapshots[k]["score"] for k in snapshots},
        "confidences": {k: snapshots[k]["confidence"] for k in snapshots},  # ✅ confidence 추가
        "statuses": {k: snapshots[k]["status"] for k in snapshots},         # ✅ status 추가
        **snapshots,   # rows + score 전체 포함
        "updated_at": datetime.now().isoformat(),
    }

    # 기존 history 불러와서 append
    existing = load_history()
    result = append_history(existing, today_snapshot)

    with open(FLOW_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ✅ 실행 결과 요약 출력
    print(f"\n[SUMMARY] {today_str}")
    for key in snapshots:
        s = snapshots[key]
        print(f"  {key:20s} → score={s['score']:+.4f} / conf={s['confidence']:.2f} / {s['status']}")

    print(f"\n[DONE] ✅ {FLOW_FILE} 저장 완료 (history={len(result.get('history', []))}일)")
