"""
validator.py — v1 FINAL (STRICT VALIDATION)
"""

import json
import sys

RESULT_PATH = "result.json"


def validate():

    try:
        with open(RESULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "date" in data, "date missing"
        assert "top10" in data, "top10 missing"

        top10 = data["top10"]

        assert isinstance(top10, list), "top10 not list"
        assert len(top10) == 10, "top10 length != 10"

        for i, item in enumerate(top10):

            assert "code" in item, f"{i} code missing"
            assert "score" in item, f"{i} score missing"
            assert "signal" in item, f"{i} signal missing"
            assert "strategy" in item, f"{i} strategy missing"

            # 타입 체크
            assert isinstance(item["code"], str)
            assert isinstance(item["score"], (int, float))

        print("[VALIDATOR] OK")
        return True

    except Exception as e:
        print(f"[VALIDATOR ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    validate()
