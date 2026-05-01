from datetime import datetime, timedelta
from pykrx import stock

# ─────────────────────────────
# 🟢 TIME WINDOW
# ─────────────────────────────
def get_window(days=5):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    return start, end


# ─────────────────────────────
# 🟢 KRX FLOW (STRICT LAYER)
# ─────────────────────────────
def get_krx_flow(krx_code: str, days: int = 5):
    start, end = get_window(days)

    try:
        df = stock.get_market_trading_fundamental(start, end, krx_code)

        if df is None or df.empty:
            return None, None, "EMPTY"

        # 명시적 컬럼 탐색 (자동추정 제거 → 안정성 우선)
        foreign_col = None
        inst_col = None

        for c in df.columns:
            if "외국인" in c and "순매수" in c:
                foreign_col = c
            if "기관" in c and "순매수" in c:
                inst_col = c

        if foreign_col is None or inst_col is None:
            raise ValueError(f"[KRX COLUMN ERROR] {list(df.columns)}")

        foreign_net = float(df[foreign_col].sum())
        inst_net = float(df[inst_col].sum())

        return foreign_net, inst_net, "OK"

    except Exception as e:
        raise ValueError(f"[KRX ERROR] {krx_code}: {str(e)}")


# ─────────────────────────────
# 🟡 DART LAYER (LENIENT)
# ─────────────────────────────
def get_dart_score(corp_code: str):
    try:
        # placeholder (existing function assumed)
        return fetch_dart_score(corp_code)
    except:
        return 0.0


# ─────────────────────────────
# 🟣 MAIN PROCESSOR
# ─────────────────────────────
def process_stock(krx_code: str, name: str, corp_map: dict, days: int = 5):

    # KRX (STRICT)
    foreign_net, inst_net, flow_status = get_krx_flow(krx_code, days)

    # DART (LENIENT)
    dart_score = 0.0
    dart_status = "FAIL"

    corp_code = corp_map.get(krx_code, {}).get("corp_code")

    if corp_code:
        try:
            dart_score = get_dart_score(corp_code)
            dart_status = "OK"
        except:
            dart_status = "FAIL"

    return {
        "code": krx_code,
        "name": name,

        # market signals
        "foreign_net": foreign_net,
        "inst_net": inst_net,
        "krx_flow_status": flow_status,

        # fundamental
        "dart_score": dart_score,
        "dart_status": dart_status
    }
