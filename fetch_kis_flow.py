"""
fetch_kis_flow.py  v5.3 (Option A Patch)
─────────────────────────────────────
KIS API → 기관/외국인 시장 수급 데이터 수집
변경사항:
  ✅ 인코딩 utf-8-sig 통일 (모바일 한글/JSON 깨짐 방지)
  ✅ 토큰 6시간 캐시 재사용 (fetch_data.py 와 공유, 403 방지)
  ✅ 에러 카운트 기반 그레이스풀 디그레이데이션 유지
  ✅ 상세 로깅 추가 (디버깅 용이)
환경변수: KIS_APP_KEY, KIS_APP_SECRET
─────────────────────────────────────
"""

import os, json, math, time, requests
from datetime import datetime, timezone, timedelta

KIS_BASE        = "https://openapi.koreainvestment.com:9443"
TIMEOUT         = 10
DELAY           = 0.5
PERCENTILE      = 0.80
HISTORY_MAX     = 20
FLOW_FILE       = "market_flow.json"
TOKEN_FILE      = "kis_token.json"
KST             = timezone(timedelta(hours=9))
ERROR_THRESHOLD = 3

_last_known   = {}
_error_counts = {}


# ───────────────────────────── AUTH (옵션 A 패치) ─────────────────────────────
def get_token() -> str:
    """kis_token.json 우선 재사용 (6시간 유효), 만료 시 신규 발급"""
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        token = data.get("access_token", "")
        issued = datetime.fromisoformat(data.get("issued_at", "1970-01-01"))
        if token and (datetime.now(KST) - issued).total_seconds() < 21600:
            print("[AUTH] 캐시된 토큰 재사용")
            return token
    except Exception:
        pass

    print("[AUTH] 토큰 새로 발급")
    res = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey":     os.environ["KIS_APP_KEY"],            "appsecret":  os.environ["KIS_APP_SECRET"],
        },
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    token = res.json()["access_token"]
    with open(TOKEN_FILE, "w", encoding="utf-8-sig") as f:
        json.dump({"access_token": token, "issued_at": datetime.now(KST).isoformat()}, f)
    return token


# ───────────────────────────── DATA PARSING ─────────────────────────────
def parse_net(value) -> int:
    try:
        v = int(str(value).replace(",", "").strip())
        return int(math.copysign(math.log1p(abs(v)), v))
    except (ValueError, TypeError):
        return 0


def dynamic_filter(rows: list) -> list:
    if not rows: return []
    thresholds = sorted(abs(r["net"]) for r in rows)
    cutoff_idx = max(0, int(len(thresholds) * (1 - PERCENTILE)) - 1)
    cutoff = thresholds[cutoff_idx]
    return [r for r in rows if abs(r["net"]) >= cutoff]


def compute_score(rows: list) -> float:
    if len(rows) < 3: return 0.0
    avg = sum(r["net"] for r in rows) / len(rows)
    return round(math.tanh(avg / 15), 4)


# ───────────────────────────── STATE MANAGEMENT ─────────────────────────────
def _key(market: str, investor: str) -> str:
    return f"{market}_{investor}"

def _update_state(key: str, score: float, confidence: float):
    _last_known[key]   = {"score": score, "confidence": confidence}
    _error_counts[key] = 0

def _handle_no_data(key: str) -> dict:
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {"score": prev["score"], "confidence": 0.3, "status": "no_data", "rows": []}

def _handle_degraded(key: str) -> dict:
    prev = _last_known.get(key, {"score": 0.5, "confidence": 0.3})
    return {"score": prev["score"], "confidence": 0.2, "status": "degraded", "rows": []}

# ───────────────────────────── HISTORY ─────────────────────────────
def load_history() -> dict:
    try:
        with open(FLOW_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}

def append_history(existing: dict, today: dict) -> dict:
    history = existing.get("history", [])
    today_date = today["date"]
    history = [h for h in history if h.get("date") != today_date]
    history.append({"date": today_date, "scores": {k: v["score"] for k, v in today.items() if isinstance(v, dict) and "score" in v}})
    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]
    today["history"] = history
    return today


# ───────────────────────────── API CALL ─────────────────────────────
def fetch_one(token: str, market: str, investor: str) -> dict:
    key     = _key(market, investor)
    url     = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd    = "0001" if market == "J" else "1001"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "tr_id":         "FHPTJ04400000",
        "custtype":      "P",
    }

    def build_params(div_cls: str):
        return {
            "FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": iscd, "FID_DIV_CLS_CODE": div_cls,
            "FID_RANK_SORT_CLS_CODE": "0", "FID_ETC_CLS_CODE": investor,
        }

    def call(params):
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    try:
        data = call(build_params("1"))        if data.get("rt_cd") == "0" and not data.get("output"):
            data = call(build_params("0"))

        if not isinstance(data, dict) or data.get("rt_cd") != "0":
            _error_counts[key] = _error_counts.get(key, 0) + 1
            if _error_counts[key] >= ERROR_THRESHOLD:
                print(f"[WARN] {key} 연속 오류 → degraded 모드")
                return _handle_degraded(key)
            return _handle_no_data(key)

        rows = [{"code": str(i.get("mksc_shrn_iscd", "")).zfill(6), "net": parse_net(i.get(net_key, 0))} for i in data.get("output", [])]
        rows       = dynamic_filter(rows)
        score      = compute_score(rows)
        confidence = 0.95
        _update_state(key, score, confidence)
        return {"score": score, "confidence": confidence, "status": "normal", "rows": rows}

    except Exception as e:
        _error_counts[key] = _error_counts.get(key, 0) + 1
        print(f"[WARN] {key} API 예외: {e}")
        if _error_counts[key] >= ERROR_THRESHOLD:
            return _handle_degraded(key)
        return _handle_no_data(key)


# ───────────────────────────── MAIN ─────────────────────────────
if __name__ == "__main__":
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    token     = get_token()

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
        "date":        today_str,
        "scores":      {k: v["score"]      for k, v in snapshots.items()},
        "confidences": {k: v["confidence"] for k, v in snapshots.items()},
        "statuses":    {k: v["status"]     for k, v in snapshots.items()},
        **snapshots,
        "updated_at":  datetime.now(KST).isoformat(),
    }
    existing = load_history()
    result   = append_history(existing, today_snapshot)

    with open(FLOW_FILE, "w", encoding="utf-8-sig") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] {FLOW_FILE} 저장 완료 (정상: {sum(1 for s in snapshots.values() if s['status']=='normal')})")
