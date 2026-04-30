import json, os

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
            hist = json.load(open(IC_FILE))
        except:
            hist = []

    hist.append(data)

    with open(IC_FILE, "w") as f:
        json.dump(hist, f, indent=2)


def compute_weights():
    return 0.6, 0.0, 0.4
