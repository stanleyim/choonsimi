import os
import sys
import json
import requests
from datetime import datetime

KIS_BASE = "https://openapi.koreainvestment.com:9443"
TIMEOUT = 10
TOP_N = 20


def get_token() -> str:
    url = f"{KIS_BASE}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": os.environ["KIS_APP_KEY"],
        "appsecret": os.environ["KIS_APP_SECRET"],
    }
    res = requests.post(url, json=body, timeout=TIMEOUT)
    res.raise_for_status()
    return res.json()["access_token"]


def fetch_kis_flow(token: str, market: str, investor: str) -> list:
    """
    market:   "J" = KOSPI, "Q" = KOSDAQ
    investor: "1" = 외국인, "2" = 기관
    """
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd = "0001" if market == "J" else "1001"
    label = "외국인" if investor == "1" else "기관"
    net_key = "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": os.environ.get("KIS_APP_KEY", ""),
        "appsecret": os.environ.get("KIS_APP_SECRET", ""),
        "tr_id": "FHPTJ04400000",
        "custtype": "P",
    }

    def build_params(div_cls: str) -> dict:
        # ✅ 매 호출마다 새 딕셔너리 → 오염 방지
        return {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": iscd,
            "FID_DIV_CLS_CODE": div_cls,
            "FID_RANK_SORT_CLS_CODE": "0",
            "FID_ETC_CLS_CODE": investor,
        }

    def request_api(params: dict) -> dict:
        res = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        res.raise_for_status()
        return res.json()

    def parse_net(value) -> int:
        # ✅ 음수 문자열 안전 처리
        try:
            return int(str(value).replace(",", "").strip()) if value else 0
        except (ValueError, TypeError):
            return 0

    try:
        print(f"[KIS] 요청 → market={market}, investor={label}")

        data = request_api(build_params("1"))
        rt = data.get("rt_cd")
        print(f"[KIS] 응답코드={rt} / msg={data.get('msg1')}")

        # fallback: 0건일 때만 div_cls=0 재시도
        if rt == "0" and not data.get("output"):
            print(f"[KIS] {market}/{label} 0건 → fallback(div_cls=0)")
            data = request_api(build_params("0"))
            print(f"[KIS] fallback 결과: {len(data.get('output', []))}건")

        if data.get("rt_cd") != "0":
            print(f"[KIS] ❌ API 오류: {data.get('msg1')}")
            return []

        output = data.get("output", [])
        if not isinstance(output, list):
            print("[KIS] ⚠️ output 구조 비정상")
            return []

        rows = []
        for item in output[:TOP_N]:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            if not code or code == "000000":
                continue
            net = parse_net(item.get(net_key, 0))
            rows.append({"code": code, "net": net})

        total = sum(r["net"] for r in rows)
        print(f"[KIS] ✅ {market}/{label} → {len(rows)}종목 / 합계순매수={total:,}")

        return rows

    except requests.exceptions.Timeout:
        print(f"[KIS] ⏱ TIMEOUT → market={market}, investor={label}")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"[KIS] 🔴 HTTP ERROR: {e}")
        return []
    except Exception as e:
        print(f"[KIS] 💥 실패: {type(e).__name__}: {e}")
        return []


# ✅ 실행 진입점 — 이게 없으면 0초 종료됨
if __name__ == "__main__":
    print(f"[START] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        token = get_token()
        print("[KIS] 토큰 발급 완료")
    except Exception as e:
        print(f"[KIS] 토큰 발급 실패: {e}")
        sys.exit(1)

    results = {}

    for market in ["J", "Q"]:
        for investor in ["1", "2"]:
            key = f"{'KOSPI' if market == 'J' else 'KOSDAQ'}_{'foreign' if investor == '1' else 'institution'}"
            results[key] = fetch_kis_flow(token, market, investor)

    results["updated_at"] = datetime.now().isoformat()

    with open("data/flow.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"[DONE] data/flow.json 저장 완료")
