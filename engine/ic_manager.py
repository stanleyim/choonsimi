import json
import os

IC_FILE = "ic_history.json"


def update_ic(flow_ic, mom_ic, dart_ic):
    data = {
        "flow_ic": flow_ic,
        "mom_ic": mom_ic,
        "dart_ic": dart_ic
    }

    hist = []
    if os.path.exists(IC_FILE):
        try:
            with open(IC_FILE, "r", encoding="utf-8") as f:
                hist = json.load(f)
        except:
            hist = []

    hist.append(data)

    with open(IC_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)


def compute_weights():
    # IC 기반 동적 보정이 없을 때도 최소 변화 구조 유지
    return 0.5, 0.2, 0.3
