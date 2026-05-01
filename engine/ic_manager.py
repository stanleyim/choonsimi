import json
import os

IC_FILE = "ic_history.json"


def update_ic(flow_ic, mom_ic, dart_ic):
    data = {
        "flow_ic": float(flow_ic) if flow_ic is not None else 0.0,
        "mom_ic": float(mom_ic) if mom_ic is not None else 0.0,
        "dart_ic": float(dart_ic) if dart_ic is not None else 0.0
    }

    hist = []
    if os.path.exists(IC_FILE):
        try:
            with open(IC_FILE, "r", encoding="utf-8") as f:
                hist = json.load(f)
                if not isinstance(hist, list):
                    hist = []
        except:
            hist = []

    hist.append(data)

    with open(IC_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def compute_weights():
    # 현재 단계에서는 안정성 우선 → 고정 weight 유지
    return {
        "flow_z": 0.6,
        "mom_z": 0.0,
        "dart_z": 0.4
    }
