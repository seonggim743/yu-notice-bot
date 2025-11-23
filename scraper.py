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

def escape_markdown_v2(text):
    """
    Escapes special characters for Telegram MarkdownV2.
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def send_telegram_message(message, is_error=False):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram token or Chat ID missing. Skipping message.")
        print(f"Message: {message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'MarkdownV2' if not is_error else 'HTML', # Use HTML for simple error messages
        'disable_web_page_preview': True
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")
        # If MarkdownV2 fails, try sending as plain text to ensure delivery
        if not is_error:
            payload['parse_mode'] = None
            try:
                requests.post(url, json=payload)
            except:
                pass

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
    try:
        state = load_state()
        
        for target in TARGETS:
            print(f"Checking {target['key']}...")
            try:
                response = requests.get(target['url'], headers=HEADERS, timeout=10)
                response.raise_for_status()
                
                new_items = parse_yu_news(response.text, state[target['key']])
                
                for item in reversed(new_items):
                    import urllib.parse
                    full_url = urllib.parse.urljoin(target['url'], item['link'])
                    
                    # MarkdownV2 Formatting
                    # Bold Title: *Title*
                    # Link: [Text](URL)
                    # Hashtag: \#Keyword (escaped)
                    
                    safe_title = escape_markdown_v2(item['title'])
                    safe_name = escape_markdown_v2(target['name'])
                    safe_url = escape_markdown_v2(full_url) # URL usually doesn't need escaping in () but good practice if it has )
                    
                    # Actually, for [text](url), the url part should NOT be escaped with backslashes generally, 
                    # but ) needs escaping if present. However, standard URLs are usually safe.
                    # Let's just escape the title and name.
                    
                    attach_mark = " 📎[첨부파일]" if item['has_attach'] else ""
                    safe_attach = escape_markdown_v2(attach_mark)
                    
                    # Identify keywords for hashtags
                    hashtags = []
                    for k in KEYWORDS:
                        if k in item['title']:
                            hashtags.append(f"#{k}")
                    
                    safe_hashtags = " ".join([escape_markdown_v2(tag) for tag in hashtags])
                    
                    msg = (
                        f"*{safe_name}*\n"
                        f"[{safe_title}]({full_url}){safe_attach}\n"
                        f"{safe_hashtags} \\#알림"
                    )
                    
                    send_telegram_message(msg)
                    state[target['key']].append(item['id'])
                    
                    if len(state[target['key']]) > 100:
                        state[target['key']] = state[target['key']][-100:]
                        
                    time.sleep(1)
                    
            except Exception as e:
                print(f"Error scraping {target['name']}: {e}")
                # Optional: Send warning for individual target failure? 
                # User asked for "Global logic" error, but individual failure is also bad.
                # Let's keep it simple as requested: Global try-except.
                raise e # Re-raise to trigger global handler
                
        save_state(state)
        
    except Exception as e:
        error_msg = f"🚨 <b>[에러 발생]</b>\n<pre>{str(e)}</pre>"
        send_telegram_message(error_msg, is_error=True)
        raise # Fail the action

if __name__ == "__main__":
    main()
