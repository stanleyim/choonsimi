import os
import pandas as pd
from datetime import datetime

# =========================
# PATH (보안 경로 고정)
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(BASE_DIR, "api/_private/history.csv")


def _normalize_code(series: pd.Series) -> pd.Series:
    """code 6자리 고정 정규화"""
    return (
        series
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"[^0-9]", "", regex=True)
        .str.zfill(6)
    )


def append_history(df: pd.DataFrame):
    """
    top 결과를 history.csv에 누적 저장 (append-only)
    - 경로: api/_private/history.csv (보안 경로 고정)
    - code 6자리 정규화 후 저장
    - date + code 기준 중복 제거 (최신 score 유지)
    """

    if df is None or df.empty:
        return

    df = df.copy()

    # =========================
    # 필수 컬럼 확인
    # =========================
    required_cols = ["code", "score"]
    for c in required_cols:
        if c not in df.columns:
            print(f"[HISTORY] 필수 컬럼 없음: {c} → skip")
            return

    # code 정규화 (6자리 고정)
    df["code"] = _normalize_code(df["code"])

    # score 안정화
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["score"])

    # date 추가
    today = datetime.now().strftime("%Y-%m-%d")
    df["date"] = today

    # =========================
    # 기존 파일 로드 후 concat
    # =========================
    if os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0:
        try:
            old = pd.read_csv(HISTORY_PATH, dtype={"code": str})
            # 기존 데이터 code도 정규화 (구버전 패딩 없는 코드 통일)
            old["code"] = _normalize_code(old["code"])
            df = pd.concat([old, df], ignore_index=True)
        except Exception as e:
            print(f"[HISTORY] 기존 파일 로드 실패 (신규 시작): {e}")

    # =========================
    # 중복 제거 (date + code 기준, 최신 score 유지)
    # =========================
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.sort_values("score", ascending=False)
    df = df.drop_duplicates(subset=["date", "code"], keep="first")
    df = df.sort_values(["date", "score"], ascending=[True, False])
    df = df.reset_index(drop=True)

    # =========================
    # 저장 (폴더 자동 생성)
    # =========================
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    df.to_csv(HISTORY_PATH, index=False, encoding="utf-8")

    print(f"[HISTORY] saved → {HISTORY_PATH} ({len(df)} rows)")
