import os, json, requests, zipfile, xml.etree.ElementTree as ET
from io import BytesIO

key = os.getenv("DART_API_KEY")
if not key:
    raise ValueError("DART_API_KEY environment variable not set")

url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"

print(" Downloading corp code from DART...")
res = requests.get(url, timeout=30)
res.raise_for_status()

z = zipfile.ZipFile(BytesIO(res.content))
xml_data = z.read(z.namelist()[0])

root = ET.fromstring(xml_data)

corp_map = {}
count = 0

for c in root.findall("list"):
    stock_code = c.findtext("stock_code")
    corp_code = c.findtext("corp_code")
    corp_name = c.findtext("corp_name")

    # KOSPI/KOSDAQ 6자리 종목코드만 저장
    if stock_code and stock_code.strip() and corp_code and corp_code.strip():
        stock_code = stock_code.strip()
        corp_map[stock_code] = {
            "corp_code": corp_code.strip(),
            "name": corp_name.strip() if corp_name else stock_code
        }
        count += 1

# UTF-8로 저장해야 한글이 깨지지 않음
with open("corp_map.json", "w", encoding="utf-8") as f:
    json.dump(corp_map, f, ensure_ascii=False, indent=2)

print(f"[OK] Saved {count} corp codes to corp_map.json")
print(f"[Sample] 005930: {corp_map.get('005930')}")
