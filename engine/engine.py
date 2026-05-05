"""
engine.py — Choonsimi FINAL ENTRY
역할: pipeline 실행만 담당
"""

import time
from engine.pipeline import run_pipeline


def main():
    # ✅ 최소 rate-limit 완화 (429 방지 보조)
    time.sleep(1)   # ⭐ 추가
    run_pipeline()


if __name__ == "__main__":
    main()
