# engine/engine.py
from datetime import datetime, timedelta
from pykrx import stock
import math

# ─────────────────────────────
# 🔧 DATE WINDOW
# ─────────────────────────────
def _get_window(days: int = 5):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    return start, end


# ─────────────────────────────
# 🟢 KRX FLOW LAYER (STRICT)
# ─────────────────────────────
def get_krx_flow(krx_code: str, days: int = 5):
    """
    return:
        foreign_net, inst_net, status
    status:
        OK / EMPTY / ERROR
    """

    start, end = _get_window(days)

    try:
        df = stock.get_market_trading_fundamental(start, end, krx_code)

        if df is None or df.empty:
            return None, None, "EMPTY"

        # 🔥 pykrx column safety mapping
        foreign_col = None
        inst_col = None

        for c in df.columns:
            if "외국인" in c:
                foreign_col = c
            if "기관" in c:
                inst_col = c

        if foreign_col is None or inst_col is None:
            return None, None, "ERROR"

        foreign_net = float(df[foreign_col].sum())
        inst_net = float(df[inst_col].sum())

        # 🔧 NaN 방어
        if math.isnan(foreign_net):
            foreign_net = 0.0
        if math.isnan(inst_net):
            inst_net = 0.0

        return foreign_net, inst_net, "OK"

    except Exception:
        return None, None, "ERROR"


# ─────────────────────────────
# 🟡 DART LAYER (LENIENT)
# ─────────────────────────────
def get_dart_score(corp_code: str):
    """
    placeholder: 기존 DART 로직 사용
    """
    try:
        # 기존 함수 연결 가정
        return 0.0
    except:
        return 0.0


# ─────────────────────────────
# 🧠 MAIN PROCESS
# ─────────────────────────────
def process_stock(krx_code: str, name: str, corp_map: dict, days: int = 5):

    # 1️⃣ KRX FLOW
    foreign_net, inst_net, flow_status = get_krx_flow(krx_code, days)

    # 2️⃣ DART MAP
    corp_code = corp_map.get(krx_code, {}).get("corp_code")

    dart_score = 0.0
    dart_status = "FAIL"

    if corp_code:
        try:
            dart_score = get_dart_score(corp_code)
            dart_status = "OK"
        except:
            dart_status = "FAIL"

    # 3️⃣ RETURN STRUCTURE (FINAL)
    return {
        "code": krx_code,
        "name": name,

        # 📊 FLOW (핵심)
        "foreign_net": foreign_net,
        "inst_net": inst_net,
        "krx_flow_status": flow_status,

        # 📊 FUNDAMENTAL
        "dart_score": dart_score,
        "dart_status": dart_status
              }
