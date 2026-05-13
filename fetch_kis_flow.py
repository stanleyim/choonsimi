"""
fetch_kis_flow.py — v6.0.0 MARKET-CLOSE GUARD
─────────────────────────────────────────────────────────
목적  : KIS API → 기관/외국인 시장 수급 데이터 수집
특징  : 장마감(15:30) 이후 실행만 저장
        장중 수동실행 → API 정상 호출 + 결과 출력 + 저장 스킵
        데이터 중복/혼재 완전 방지
─────────────────────────────────────────────────────────
v6.0.0 vs v5.3 변경점
  ✔ 장중 실행 시 저장 스킵 (기존 데이터 보존)
  ✔ rows=[] → status="no_data" 버그 수정
  ✔ rows=[] → 전일 정상 score 유지 (0.0 저장 방지)
  ✔ 날짜 중복 방지 (오늘 날짜 제거 후 저장)
  ✔ history 날짜 중복 방지 (날짜별 최신 1개만 유지)
─────────────────────────────────────────────────────────
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
MARKET_CLOSE    = 15 * 60 + 30   # 15:30 KST (분 단위)


# ══════════════════════════════════════════════════════════
# 장마감 여부 확인
# ══════════════════════════════════════════════════════════
def is_market_closed() -> bool:
    now = datetime.now(KST)
    return (now.hour * 60 + now.minute) >= MARKET_CLOSE


# ══════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════
def get_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        token  = data.get("access_token", "")
        issued = datetime.fromisoformat(
            data.get("issued_at", "1970-01-01").replace("Z", "")
        )
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=KST)
        if token and (datetime.now(KST) - issued).total_seconds() < 21600:
            print("[AUTH] 캐시 토큰 사용")
            return token
    except: pass

    print("[AUTH] 신규 토큰 발급")
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
    token = res.json()["access_token"]

    with open(TOKEN_FILE, "w", encoding="utf-8-sig") as f:
        json.dump({
            "access_token": token,
            "issued_at":    datetime.now(KST).isoformat()
        }, f, ensure_ascii=False)

    return token


# ══════════════════════════════════════════════════════════
# PARSE / SCORE
# ══════════════════════════════════════════════════════════
def parse_net(value) -> int:
    try:
        v = int(str(value).replace(",", "").strip())
        return int(math.copysign(math.log1p(abs(v)), v))
    except: return 0

def dynamic_filter(rows: list) -> list:
    if not rows: return []
    thresholds = sorted(abs(r["net"]) for r in rows)
    cutoff_idx = max(0, int(len(thresholds) * (1 - PERCENTILE)) - 1)
    cutoff     = thresholds[cutoff_idx]
    return [r for r in rows if abs(r["net"]) >= cutoff]

def compute_score(rows: list) -> float:
    if len(rows) < 3: return 0.0
    avg = sum(r["net"] for r in rows) / len(rows)
    return round(math.tanh(avg / 15), 4)


# ══════════════════════════════════════════════════════════
# 기존 파일에서 전일 정상 score 조회
# rows=[] 일 때 0.0 저장 방지용
# ══════════════════════════════════════════════════════════
def get_prev_score(existing: dict, key: str) -> float:
    """history에서 가장 최근 정상 score 반환"""
    history = existing.get("history", [])
    # 최신순 탐색
    for h in reversed(history):
        score = h.get("scores", {}).get(key)
        if score is not None and score != 0.0:
            return score
    return 0.5   # 기본값


# ══════════════════════════════════════════════════════════
# API 호출
# ══════════════════════════════════════════════════════════
def fetch_one(token: str, market: str, investor: str,
              existing: dict, seg_key: str) -> dict:
    url  = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd = "0001" if market == "J" else "1001"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    hdrs = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "tr_id":         "FHPTJ04400000",
        "custtype":      "P",
    }

    def build_params(div_cls: str):
        return {
            "FID_COND_MRKT_DIV_CODE": "V",
            "FID_COND_SCR_DIV_CODE":  "16449",
            "FID_INPUT_ISCD":         iscd,
            "FID_DIV_CLS_CODE":       div_cls,
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE":       investor,
        }

    def call(params):
        r = requests.get(url, headers=hdrs, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    try:
        data = call(build_params("1"))
        if data.get("rt_cd") == "0" and not data.get("output"):
            data = call(build_params("0"))

        if not isinstance(data, dict) or data.get("rt_cd") != "0":
            prev = get_prev_score(existing, seg_key)
            print(f"  [{seg_key}] API 오류 → 전일 score 유지: {prev}")
            return {
                "score":      prev,
                "confidence": 0.2,
                "status":     "api_error",
                "rows":       [],
            }

        rows = [
            {
                "code": str(i.get("mksc_shrn_iscd", "")).zfill(6),
                "net":  parse_net(i.get(net_key, 0)),
            }
            for i in data.get("output", [])
        ]
        rows = dynamic_filter(rows)

        # ✅ rows=[] → no_data, 전일 score 유지
        if not rows:
            prev = get_prev_score(existing, seg_key)
            print(f"  [{seg_key}] 데이터 없음 → 전일 score 유지: {prev}")
            return {
                "score":      prev,
                "confidence": 0.3,
                "status":     "no_data",
                "rows":       [],
            }

        score = compute_score(rows)
        print(f"  [{seg_key}] score={score}  rows={len(rows)}")
        return {
            "score":      score,
            "confidence": 0.95,
            "status":     "normal",
            "rows":       rows,
        }

    except Exception as e:
        prev = get_prev_score(existing, seg_key)
        print(f"  [{seg_key}] 예외: {e} → 전일 score 유지: {prev}")
        return {
            "score":      prev,
            "confidence": 0.2,
            "status":     "error",
            "rows":       [],
        }


# ══════════════════════════════════════════════════════════
# 기존 파일 로드
# ══════════════════════════════════════════════════════════
def load_existing() -> dict:
    try:
        with open(FLOW_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except: return {}


# ══════════════════════════════════════════════════════════
# history 업데이트 (날짜 중복 방지)
# ══════════════════════════════════════════════════════════
def build_history(existing: dict, today_str: str, snapshots: dict) -> list:
    history = existing.get("history", [])

    # ✅ 날짜 중복 제거 (같은 날짜 기존 항목 삭제)
    history = [h for h in history if h.get("date") != today_str]

    # 오늘 항목 추가
    history.append({
        "date":   today_str,
        "scores": {k: v["score"] for k, v in snapshots.items()}
    })

    # 최대 HISTORY_MAX 유지
    if len(history) > HISTORY_MAX:
        history = history[-HISTORY_MAX:]

    return history


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    today_str  = datetime.now(KST).strftime("%Y-%m-%d")
    closed     = is_market_closed()

    print(f"[START] fetch_kis_flow v6.0.0  {today_str}")
    print(f"[TIME]  장마감 {'이후' if closed else '이전 (테스트 모드 — 저장 스킵)'}")

    token    = get_token()
    existing = load_existing()

    SEGMENTS = [
        ("J", "1", "KOSPI_foreign"),
        ("J", "2", "KOSPI_institution"),
        ("Q", "1", "KOSDAQ_foreign"),
        ("Q", "2", "KOSDAQ_institution"),
    ]

    snapshots = {}
    for market, investor, seg_key in SEGMENTS:
        snapshots[seg_key] = fetch_one(token, market, investor, existing, seg_key)
        time.sleep(DELAY)

    # ── 결과 출력 (장중/장후 공통) ───────────────────────
    normal_cnt  = sum(1 for v in snapshots.values() if v["status"] == "normal")
    nodata_cnt  = sum(1 for v in snapshots.values() if v["status"] == "no_data")
    error_cnt   = sum(1 for v in snapshots.values() if v["status"] in ("api_error","error"))

    print(f"[RESULT] 정상={normal_cnt} / 데이터없음={nodata_cnt} / 오류={error_cnt}")
    for k, v in snapshots.items():
        print(f"  {k}: score={v['score']}  status={v['status']}  rows={len(v['rows'])}")

    # ── 장중이면 저장 스킵 ───────────────────────────────
    if not closed:
        print("[SKIP] 장중 실행 — 파일 저장 스킵 (기존 데이터 보존)")
        exit(0)

    # ── 장마감 후 저장 ───────────────────────────────────
    history = build_history(existing, today_str, snapshots)

    result = {
        "date": today_str,
        "scores":      {k: v["score"]      for k, v in snapshots.items()},
        "confidences": {k: v["confidence"] for k, v in snapshots.items()},
        "statuses":    {k: v["status"]     for k, v in snapshots.items()},
        **snapshots,
        "updated_at": datetime.now(KST).isoformat(),
        "history":    history,
    }

    with open(FLOW_FILE, "w", encoding="utf-8-sig") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[DONE] market_flow.json 저장 완료 | history {len(history)}일치 보존")
