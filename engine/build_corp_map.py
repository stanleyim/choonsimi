import requests
import xml.etree.ElementTree as ET
import json
import os
import zipfile
import io

API_KEY = os.getenv("DART_API_KEY")
URL = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"

print("[START] Downloading corp codes from DART...")

# ---------- 다운로드 ----------
res = requests.get(URL, timeout=30)
res.raise_for_status()

z = zipfile.ZipFile(io.BytesIO(res.content))
xml_data = z.read(z.namelist()[0]).decode("utf-8")

root = ET.fromstring(xml_data)

corp_map = {}
count = 0
filtered = 0

# ---------- 파싱 ----------
for item in root.findall("list"):
    stock_code = (item.findtext("stock_code") or "").strip()
    corp_code = (item.findtext("corp_code") or "").strip()
    corp_name = (item.findtext("corp_name") or "").strip()

    # 기본 필터
    if not stock_code or not corp_code or len(stock_code) != 6:
        continue

    # 🔥 품질 필터 (중요)
    name_upper = corp_name.upper()

    # SPAC / 스팩 제거
    if "스팩" in corp_name or "SPAC" in name_upper:
        filtered += 1
        continue

    # ETF / ETN 제거
    if "ETF" in name_upper or "ETN" in name_upper:
        filtered += 1
        continue

    # 우선주 제거 (선택)
    if stock_code.endswith("5"):   # KRX 우선주 패턴
        filtered += 1
        continue

    corp_map[stock_code] = {
        "corp_code": corp_code,
        "name": corp_name
    }
    count += 1

# ---------- 검증 ----------
if count < 2000:
    raise RuntimeError(f"[FAIL] corp_map too small: {count}")

# 샘플 검증 (삼성전자)
sample = corp_map.get("005930")
if not sample:
    raise RuntimeError("[FAIL] Samsung Electronics missing")

# ---------- 저장 (atomic write) ----------
tmp_path = "corp_map.json.tmp"
final_path = "corp_map.json"

with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(corp_map, f, ensure_ascii=False, indent=2)

os.replace(tmp_path, final_path)

# ---------- 로그 ----------
print(f"[OK] corp_map.json generated")
print(f" - total: {count}")
print(f" - filtered: {filtered}")
print(f" - sample 005930: {sample}")
