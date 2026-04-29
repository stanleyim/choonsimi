import os, json, math, shutil, requests
from datetime import datetime, timedelta

OUTPUT_PATH = "data.json"
BACKUP_PATH = "data.json.bak"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KOSPI_URL = f"{KRX_BASE}/stk_bydd_trd"
KOSDAQ_URL = f"{KRX_BASE}/ksq_bydd_trd"


# ─────────────────────────────
# SAFE PARSER
# ─────────────────────────────
def safe_int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except:
        return 0


# ─────────────────────────────
# DATE (3 DAY FALLBACK)
# ─────────────────────────────
def get_dates():
    base = datetime.now()
    return [(base - timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]


# ─────────────────────────────
# KRX CALL
# ─────────────────────────────
def call_krx(url, date):
    try:
        r = requests.get(
            url,
            params={"basDd": date},
            headers={"AUTH_KEY": os.getenv("KRX_API_KEY")},
            timeout=5
        )
        j = r.json()
        return j.get("OutBlock_1") or j.get("block1") or []
    except:
        return []


# ─────────────────────────────
# LOAD MARKET DATA
# ─────────────────────────────
def load_market():
    for d in get_dates():
        kospi = call_krx(KOSPI_URL, d)
        kosdaq = call_krx(KOSDAQ_URL, d)

        data = kospi + kosdaq

        if len(data) > 0:
            return data, d

    return [], None


# ─────────────────────────────
# UNIVERSE (TOP 200 MKT CAP)
# ─────────────────────────────
def get_universe(items):
    cleaned = []

    for s in items:
        code = s.get("ISU_CD")
        mcap = safe_int(s.get("MKTCAP", 0))

        if code and mcap > 0:
            cleaned.append((code, mcap))

    cleaned.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in cleaned[:200]]


# ─────────────────────────────
# FEATURES
# ─────────────────────────────
def features(s):
    close = safe_int(s.get("TDD_CLSPRC", 0))
    vol = safe_int(s.get("ACC_TRDVOL", 0))

    momentum = math.log1p(close)
    liquidity = math.log1p(vol)
    risk = 1 / (1 + math.log1p(abs(vol) + 1))

    return momentum, liquidity, risk


# ─────────────────────────────
# SCORE ENGINE
# ─────────────────────────────
def score(m, l, r, rank):
    size = 20 - (rank / 200 * 20)

    return (
        size * 0.2 +
        m * 0.4 +
        l * 0.2 +
        r * 0.2
    )


# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main():
    print("[ENGINE v7.3 CLEAN START]")

    market, used_date = load_market()

    if len(market) < 50:
        print("[SKIP] insufficient market data")
        return

    universe = get_universe(market)

    results = []

    for i, code in enumerate(universe, 1):
        s = next((x for x in market if x.get("ISU_CD") == code), None)
        if not s:
            continue

        m, l, r = features(s)
        sc = score(m, l, r, i)

        results.append({
            "code": code,
            "score": round(sc, 4)
        })

    results.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "time": datetime.now().isoformat(),
        "data_date": used_date,
        "mode": "v7.3_production_clean",
        "top10": results[:10],
        "all": results
    }

    if os.path.exists(OUTPUT_PATH):
        shutil.copy(OUTPUT_PATH, BACKUP_PATH)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[DONE] {len(results)} stocks")


if __name__ == "__main__":
    main()
