import os, json, requests, zipfile, xml.etree.ElementTree as ET
from io import BytesIO

key = os.getenv("DART_API_KEY")
url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}"

res = requests.get(url)
z = zipfile.ZipFile(BytesIO(res.content))
xml = z.read(z.namelist()[0])

root = ET.fromstring(xml)

corp_map = {
    c.findtext("stock_code").strip(): c.findtext("corp_code").strip()
    for c in root.findall("list")
    if c.findtext("stock_code")
}

with open("corp_map.json", "w") as f:
    json.dump(corp_map, f)

print(len(corp_map))
