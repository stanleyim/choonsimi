def fetch_kis_flow(token: str, market: str, investor: str) -> list:

    url = f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    iscd = "0001" if market == "J" else "1001"

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": os.environ.get("KIS_APP_KEY", ""),
        "appsecret": os.environ.get("KIS_APP_SECRET", ""),
        "tr_id": "FHPTJ04400000",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": iscd,
        "FID_DIV_CLS_CODE": "1",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_ETC_CLS_CODE": investor,
    }

    def request_api(p):
        res = requests.get(url, headers=headers, params=p, timeout=TIMEOUT)
        res.raise_for_status()
        return res.json()

    try:
        print(f"[KIS] 요청 시작 → market={market}, investor={investor}")

        data = request_api(params)

        print(f"[DEBUG] 응답코드: {data.get('rt_cd')} / msg: {data.get('msg1')}")

        # ✅ fallback (데이터 0건일 때만)
        if data.get("rt_cd") == "0" and not data.get("output"):
            print(f"[KIS] {market}/{investor} 0건 → fallback 실행")

            params["FID_DIV_CLS_CODE"] = "0"
            data = request_api(params)

            print(f"[DEBUG] fallback 응답: {data.get('msg1')} / 건수: {len(data.get('output', []))}")

        # ❌ API 실패
        if data.get("rt_cd") != "0":
            print(f"[KIS] 오류: {data.get('msg1')}")
            return []

        output = data.get("output", [])
        if not isinstance(output, list):
            print("[KIS] ⚠️ output 비정상 구조")
            return []

        rows = []
        for item in output[:TOP_N]:
            code = str(item.get("mksc_shrn_iscd", "")).zfill(6)

            net = int(
                item.get(
                    "frgn_ntby_qty" if investor == "1" else "orgn_ntby_qty",
                    0
                ) or 0
            )

            if code and code != "000000":
                rows.append({
                    "code": code,
                    "net": net
                })

        # ✅ 핵심 디버깅 로그
        total_net = sum(r["net"] for r in rows)
        label = "외국인" if investor == "1" else "기관"

        print(f"[KIS] {market}/{label} → {len(rows)}종목 / 합계: {total_net}")

        if not rows:
            print("[KIS] ⚠️ 데이터 없음 (빈 결과)")

        return rows

    except requests.exceptions.Timeout:
        print(f"[KIS] TIMEOUT → market={market}, investor={investor}")
        return []

    except requests.exceptions.HTTPError as e:
        print(f"[KIS] HTTP ERROR: {e}")
        return []

    except Exception as e:
        print(f"[KIS] 실패: {e}")
        return []
