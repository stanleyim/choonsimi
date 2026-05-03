"""
history_manager.py — v1 FINAL (ROBUST HISTORY)
"""

import pandas as pd
import os

HISTORY_PATH = "history.csv"


def update_history(df, date):

    if df is None or len(df) == 0:
        print("[HISTORY] 입력 데이터 없음")
        return

    df = df.copy()

    df["date"] = date

    keep_cols = ["date", "code", "close", "score"]
    for col in keep_cols:
        if col not in df.columns:
            df[col] = 0.0

    df = df[keep_cols]

    # 기존 데이터 로드
    if os.path.exists(HISTORY_PATH):
        old = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        df = pd.concat([old, df], ignore_index=True)

    # 중복 제거
    df = df.drop_duplicates(subset=["date", "code"], keep="last")

    # 정렬
    df = df.sort_values(["date", "code"])

    df.to_csv(HISTORY_PATH, index=False)

    print(f"[HISTORY] 저장 완료: {len(df)} rows")
