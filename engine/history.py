import os
import pandas as pd
from datetime import datetime

# =========================
# PATH (보안 경로 고정)
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(BASE_DIR, "api/_private/history.csv")


def append_history(df: pd.DataFrame):
    """
    top 결과를 history.csv에 누적 저장 (append-only)
    경로: api/_private/history.csv (보안 경로 고정)
    """

    if df is None or df.empty:
        return

    df = df.copy()

    # =========================
    # 필수 컬럼 정리
    # =========================
    required_cols = ["code", "score"]
    for c in required_cols:
        if c not in df.columns:
            return

    # code 정규화
    df["code"] = (
        df["code"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )

    # score 안정화
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["score"])

    # date 추가
    today = datetime.now().strftime("%Y-%m-%d")
    df["date"] = today

    # =========================
    # 기존 파일 로드 후 concat
    # =========================
    if os.path.exists(HISTORY_PATH):
        old = pd.read_csv(HISTORY_PATH)
        df = pd.concat([old, df], ignore_index=True)

    # =========================
    # 저장 (폴더 자동 생성)
    # =========================
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    df.to_csv(HISTORY_PATH, index=False, encoding="utf-8")

    print(f"[HISTORY] saved → {HISTORY_PATH} ({len(df)} rows)")
