import json

d = json.load(open("result.json"))

# 기본 구조 검증
assert "top10" in d, "top10 missing"
assert len(d["top10"]) == 10, "top10 not 10"

for x in d["top10"]:
    assert "code" in x, "code missing"
    assert "score" in x, "score missing"

print("VALID OK")
