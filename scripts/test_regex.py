import re
from urllib.parse import unquote

header = "attachment; filename*=UTF-8''%287%EA%B8%B0%20%EC%A0%84%EA%B3%B5%EB%A9%98%ED%86%A0%EB%8B%A8%20%EC%A7%80%EC%9B%90%EC%84%9C%20%EB%B0%8F%20%EC%9E%90%EA%B8%B0%EC%86%8C%EA%B0%9C%EC%84%9C%29%ED%95%99%EA%B3%BC_%EC%9D%B4%EB%A6%84_%ED%95%99%EB%B2%88.hwp"

print(f"Header: {header}")

# Current Regex
regex = r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)'
match = re.search(regex, header)

if match:
    extracted = match.group(1)
    print(f"Extracted: {extracted}")
    decoded = unquote(extracted)
    print(f"Decoded: {decoded}")
else:
    print("No match found!")
