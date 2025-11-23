import os
import json
import time
import logging
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from supabase import create_client, Client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import List, Dict, Optional, Any
import urllib.parse

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- Constants ---
CONFIG_FILE = 'config.json'
GEMINI_MODEL = 'gemini-1.5-flash'

class NoticeScraper:
    def __init__(self):
        self.config = self._load_config()
        self.session = self._init_session()
        self.supabase = self._init_supabase()
        self._init_gemini()
        
        self.telegram_token = os.environ.get('TELEGRAM_TOKEN')
        self.chat_id = os.environ.get('CHAT_ID')

    def _load_config(self) -> Dict[str, Any]:
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Config file {CONFIG_FILE} not found.")
            raise

    def _init_session(self) -> requests.Session:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        session.headers.update({'User-Agent': self.config['user_agent']})
        return session

    def _init_supabase(self) -> Optional[Client]:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            logger.warning("Supabase credentials missing. State persistence will be disabled.")
            return None
        return create_client(url, key)

    def _init_gemini(self):
        api_key = os.environ.get('GEMINI_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
        else:
            logger.warning("Gemini API Key missing. AI Summary will be disabled.")

    def get_last_id(self, site_key: str) -> Optional[str]:
        if not self.supabase:
            return None
        try:
            response = self.supabase.table('crawling_logs').select('last_post_id').eq('site_name', site_key).execute()
            if response.data:
                return response.data[0]['last_post_id']
        except Exception as e:
            logger.error(f"Failed to fetch last ID for {site_key}: {e}")
        return None

    def update_last_id(self, site_key: str, new_id: str):
        if not self.supabase:
            return
        try:
            # Upsert logic
            data = {'site_name': site_key, 'last_post_id': new_id}
            self.supabase.table('crawling_logs').upsert(data).execute()
            logger.info(f"Updated last ID for {site_key} to {new_id}")
        except Exception as e:
            logger.error(f"Failed to update last ID for {site_key}: {e}")

    def fetch_page(self, url: str) -> Optional[str]:
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def get_ai_summary(self, url: str, selector: str) -> Optional[str]:
        if not os.environ.get('GEMINI_API_KEY'):
            return None

        html = self.fetch_page(url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')
        content_div = soup.select_one(selector)
        if not content_div:
            return None

        text = content_div.get_text(separator=' ', strip=True)
        if len(text) < 50:
            return None

        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = (
                f"이 공지사항 내용을 한국어로 요약해줘. "
                f"텔레그램 알림용이므로 3개의 불렛 포인트(•)로 요약하고, "
                f"문장은 명사형(~함, ~임)으로 끝내줘.\n\n"
                f"내용:\n{text[:3000]}"
            )
            response = model.generate_content(prompt)
            time.sleep(4) # Rate limiting
            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Summary failed: {e}")
            return None

    def escape_markdown_v2(self, text: str) -> str:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

    def send_telegram(self, message: str, is_error: bool = False):
        if not self.telegram_token or not self.chat_id:
            logger.warning("Telegram credentials missing.")
            return

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'MarkdownV2' if not is_error else 'HTML',
            'disable_web_page_preview': True
        }
        
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            if not is_error:
                payload['parse_mode'] = None
                try:
                    self.session.post(url, json=payload)
                except:
                    pass

    def parse_list(self, html: str, last_id: Optional[str], keywords: List[str]) -> List[Dict]:
        soup = BeautifulSoup(html, 'html.parser')
        new_items = []
        rows = soup.select('table tbody tr')

        for row in rows:
            try:
                cols = row.find_all('td')
                if not cols: continue

                title_link = row.select_one('a')
                if not title_link: continue

                title = title_link.get_text(strip=True)
                link = title_link.get('href')
                
                parsed_url = urllib.parse.urlparse(link)
                qs = urllib.parse.parse_qs(parsed_url.query)
                article_id = qs.get('articleNo', [None])[0]
                
                if not article_id:
                    article_id = title # Fallback

                # Stop if we reached the last seen ID
                # Note: This assumes the list is ordered by date DESC
                if last_id and article_id == last_id:
                    break

                if not any(k in title for k in keywords):
                    continue

                # Check attachment
                is_attach = False
                if row.select('.b-file-btn') or row.select('.b-icon-file'):
                    is_attach = True
                else:
                    for img in row.find_all('img'):
                        if 'file' in img.get('src', '').lower():
                            is_attach = True
                            break

                new_items.append({
                    'id': article_id,
                    'title': title,
                    'link': link,
                    'has_attach': is_attach
                })
            except Exception as e:
                logger.error(f"Error parsing row: {e}")
                continue
        
        return new_items

    def run(self):
        try:
            for target in self.config['targets']:
                logger.info(f"Checking {target['name']}...")
                
                last_id = self.get_last_id(target['key'])
                html = self.fetch_page(target['url'])
                
                if not html: continue

                new_items = self.parse_list(html, last_id, self.config['keywords'])
                
                if not new_items:
                    logger.info("No new items found.")
                    continue

                # Process oldest first
                for item in reversed(new_items):
                    full_url = urllib.parse.urljoin(target['url'], item['link'])
                    
                    # Prepare Message
                    safe_title = self.escape_markdown_v2(item['title'])
                    safe_name = self.escape_markdown_v2(target['name'])
                    
                    attach_mark = " 📎[첨부파일]" if item['has_attach'] else ""
                    safe_attach = self.escape_markdown_v2(attach_mark)
                    
                    hashtags = [f"#{k}" for k in self.config['keywords'] if k in item['title']]
                    safe_hashtags = " ".join([self.escape_markdown_v2(tag) for tag in hashtags])
                    
                    # AI Summary
                    summary_section = ""
                    summary = self.get_ai_summary(full_url, target['content_selector'])
                    if summary:
                        safe_summary = self.escape_markdown_v2(summary)
                        summary_section = f"\n\n🤖 *AI 3줄 요약*\n{safe_summary}"

                    msg = (
                        f"*{safe_name}*\n"
                        f"[{safe_title}]({full_url}){safe_attach}\n"
                        f"{summary_section}\n"
                        f"{safe_hashtags} \\#알림"
                    )
                    
                    self.send_telegram(msg)
                    
                    # Update last ID immediately to avoid duplicate alerts if crash happens
                    self.update_last_id(target['key'], item['id'])
                    
                    time.sleep(1)

        except Exception as e:
            error_msg = f"🚨 <b>[에러 발생]</b>\n<pre>{str(e)}</pre>"
            self.send_telegram(error_msg, is_error=True)
            raise

if __name__ == "__main__":
    Scraper = NoticeScraper()
    Scraper.run()
