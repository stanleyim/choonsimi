# (기존 동일 코드 유지)

def fetch_investor_flow(token: str, market: str, investor: str) -> list:
    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"

    iscd = "0001" if market == "J" else "1001"

    headers = {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        os.environ["KIS_APP_KEY"],
        "appsecret":     os.environ["KIS_APP_SECRET"],
        "tr_id":         "FHPTJ04400000",
        "custtype":      "P",
    }

    # ✅ 1차 시도
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE":  "16449",
        "FID_INPUT_ISCD":         iscd,
        "FID_DIV_CLS_CODE":       "1",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_ETC_CLS_CODE":       investor,
    }

    def request_api(p):
        res = requests.get(url, headers=headers, params=p, timeout=TIMEOUT)
        res.raise_for_status()
        return res.json()

    try:
        data = request_api(params)

        # ✅ 디버깅 로그 추가
        print(f"[DEBUG] {market}/{investor} 응답:", data.get("msg1", ""), "건수:", len(data.get("output", [])))

        # ❗ 0건이면 fallback 1회
        if data.get("rt_cd") == "0" and not data.get("output"):
            print(f"[KIS] {market}/{investor} 0건 → fallback 재시도")

            params["FID_DIV_CLS_CODE"] = "0"   # ⭐ fallback
            data = request_api(params)

        if data.get("rt_cd") != "0":
            print(f"[KIS] 오류: {data.get('msg1')}")
            return []

        rows = []
        for item in data.get("output", [])[:TOP_N]:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)
            net = int(item.get("frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty", 0) or 0)
            if code and code != "000000":
                rows.append({"code": code, "net": net})

        print(f"[KIS] {market}/{investor} → {len(rows)}종목")
        return rows

    except Exception as e:
        print(f"[KIS] 실패: {e}")
        return []
