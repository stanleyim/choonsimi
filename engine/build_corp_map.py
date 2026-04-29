import requests
import xml.etree.ElementTree as ET
import json
import os
import zipfile
import io

API_KEY = os.environ['DART_API_KEY']
URL = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"

response = requests.get(URL, timeout=30)
response.raise_for_status()

z = zipfile.ZipFile(io.BytesIO(response.content))
xml_data = z.read(z.namelist()[0]).decode('utf-8')

root = ET.fromstring(xml_data)

corp_map = {}
for item in root.findall('list'):
    stock_code = (item.find('stock_code').text or "").strip()
    corp_code = (item.find('corp_code').text or "").strip()
    corp_name = (item.find('corp_name').text or "").strip()

    if not stock_code or not corp_code or len(stock_code) != 6:
        continue

    corp_map[stock_code] = {
        "corp_code": corp_code,
        "name": corp_name
    }

# 검증 (중요)
if len(corp_map) < 3000:
    raise RuntimeError(f"corp_map too small: {len(corp_map)}")

# atomic write
tmp = "corp_map.json.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(corp_map, f, ensure_ascii=False, indent=2)

os.replace(tmp, "corp_map.json")

print(f"[OK] corp_map.json generated: {len(corp_map)} entries")
