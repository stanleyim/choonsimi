import json
import numpy as np

def update_ic(ic):
    if ic is None:
        ic = 0.0

    with open("ic_history.csv", "a") as f:
        f.write(f"{ic}\n")


def compute_weights(ic):
    if ic is None or np.isnan(ic):
        return {"mom_z": 0.5, "dart_z": 0.5}

    return {
        "mom_z": 0.7,
        "dart_z": 0.3
    }
