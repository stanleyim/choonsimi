"""
publish.py — v1 FINAL (SAFE PUBLISH)
"""

import json
import os
import requests
from datetime import datetime

RESULT_PATH = "result.json"

API_URL = os.environ.get("APP_API_URL", "")


def publish():
    if not API_URL:
        print("[PUBLISH] APP_API_URL 없음 → skip")
        return True  # 파이프라인 죽이지 않음

    try:
        with open(RESULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 최소 검증
        if "top10" not in data or len(data["top10"]) != 10:
            print("[PUBLISH] 데이터 이상 → 전송 중단")
            return False

        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "data": data
        }

        res = requests.post(API_URL, json=payload, timeout=10)

        print(f"[PUBLISH] status={res.status_code}")

        if res.status_code != 200:
            print("[PUBLISH] 서버 오류")
            return False

        return True

    except Exception as e:
        print(f"[PUBLISH ERROR] {e}")
        return False


if __name__ == "__main__":
    publish()
