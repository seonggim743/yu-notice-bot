import requests
from bs4 import BeautifulSoup
import sys

# Force UTF-8 for stdout
sys.stdout.reconfigure(encoding='utf-8')

url = "https://hcms.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227494880"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

try:
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Text unique to the article
    target_text = "공학교육혁신센터에서는"
    element = soup.find(string=lambda text: target_text in text if text else False)
    
    if element:
        parent = element.parent
        print(f"Found text in tag: {parent.name}")
        print(f"Classes: {parent.get('class')}")
        print(f"ID: {parent.get('id')}")
        
        # Go up to find a div with a class
        curr = parent
        for _ in range(5):
            curr = curr.parent
            if curr:
                print(f"Parent ({curr.name}): Class={curr.get('class')}, ID={curr.get('id')}")
            else:
                break
    else:
        print("Text not found.")
        
except Exception as e:
    print(f"Error: {e}")
