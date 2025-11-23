import requests
from bs4 import BeautifulSoup
import json
import os
import sys
import time

# Force UTF-8 for stdout/stderr to handle emojis on Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
KEYWORDS = ['장학', 'SW', '인턴', '채용', '공모전', '대회']  # User-defined keywords
STATE_FILE = 'latest_ids.json'

TARGETS = [
    {
        'key': 'yu_news',
        'url': 'https://hcms.yu.ac.kr/main/intro/yu-news.do',
        'base_url': 'https://hcms.yu.ac.kr',
        'name': '📢 영남대 대학 뉴스'
    },
    {
        'key': 'cse_notice',
        'url': 'https://www.yu.ac.kr/cse/community/notice.do',
        'base_url': 'https://www.yu.ac.kr',
        'name': '💻 컴공 공지사항'
    }
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {t['key']: [] for t in TARGETS}

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=4)

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram token or Chat ID missing. Skipping message.")
        print(f"Message: {message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def has_attachment(row):
    # Check for common attachment indicators
    # 1. Image with 'file' or 'attach' in src or alt
    # 2. 'disk' icon class often used in Korean CMS
    # 3. Explicit text like '첨부파일'
    
    # Check images
    imgs = row.find_all('img')
    for img in imgs:
        src = img.get('src', '').lower()
        alt = img.get('alt', '').lower()
        if 'file' in src or 'attach' in src or 'disk' in src or 'file' in alt:
            return True
            
    # Check specific classes (common in YU CMS)
    if row.select('.b-file-btn') or row.select('.b-icon-file'):
        return True
        
    return False

def parse_yu_news(html, seen_ids):
    soup = BeautifulSoup(html, 'html.parser')
    new_posts = []
    
    # Generic table row selector - adjust based on actual structure if needed
    # Usually rows are in <tbody> -> <tr>
    rows = soup.select('table tbody tr')
    
    for row in rows:
        try:
            # Skip notice/header rows if they don't have a standard number
            # Often the first column is the number or '공지'
            cols = row.find_all('td')
            if not cols:
                continue
                
            # Title is usually in the second or third column, inside an <a> tag
            title_link = row.select_one('a')
            if not title_link:
                continue
                
            title = title_link.get_text(strip=True)
            link = title_link.get('href')
            
            # Extract ID from link (e.g., articleNo=12345)
            # If link is relative, make it absolute
            if link and not link.startswith('http'):
                # Handle javascript links or relative paths
                pass # Logic handled below
            
            # Simple ID extraction: use the full link as ID if unique parameter exists
            # Or hash the title + date if no ID. 
            # YU URLs usually have 'articleNo'
            import urllib.parse
            parsed_url = urllib.parse.urlparse(link)
            qs = urllib.parse.parse_qs(parsed_url.query)
            article_id = qs.get('articleNo', [None])[0]
            
            if not article_id:
                # Fallback: use title as ID (risky but better than nothing)
                article_id = title
            
            if article_id in seen_ids:
                continue
                
            # Check keywords
            if not any(k in title for k in KEYWORDS):
                continue
                
            # Check attachment
            is_attach = has_attachment(row)
            
            new_posts.append({
                'id': article_id,
                'title': title,
                'link': link,
                'has_attach': is_attach
            })
            
        except Exception as e:
            print(f"Error parsing row: {e}")
            continue
            
    return new_posts

def main():
    state = load_state()
    
    for target in TARGETS:
        print(f"Checking {target['key']}...")
        try:
            response = requests.get(target['url'], headers=HEADERS, timeout=10)
            response.raise_for_status()
            
            # Currently using the same parser for both as they share the CMS platform usually
            # If they differ significantly, we can split the logic
            new_items = parse_yu_news(response.text, state[target['key']])
            
            # Process new items (oldest first to maintain order in chat)
            for item in reversed(new_items):
                # Construct absolute URL
                import urllib.parse
                full_url = urllib.parse.urljoin(target['url'], item['link'])
                
                attach_mark = " 📎[첨부파일]" if item['has_attach'] else ""
                
                msg = (
                    f"<b>{target['name']}</b>\n"
                    f"<a href='{full_url}'>{item['title']}</a>{attach_mark}\n"
                    f"#알림"
                )
                
                send_telegram_message(msg)
                state[target['key']].append(item['id'])
                
                # Keep state manageable (last 100 IDs)
                if len(state[target['key']]) > 100:
                    state[target['key']] = state[target['key']][-100:]
                    
                time.sleep(1) # Rate limit for Telegram
                
        except Exception as e:
            print(f"Error scraping {target['name']}: {e}")
            
    save_state(state)

if __name__ == "__main__":
    main()
