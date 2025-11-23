import os
import json
import time
import datetime
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

    def get_ai_analysis(self, url: str, selector: str) -> Dict[str, Any]:
        if not os.environ.get('GEMINI_API_KEY'):
            return {"useful": True, "category": "일반", "summary": "AI Key Missing"}

        html = self.fetch_page(url)
        if not html:
            return {"useful": False, "category": "일반", "summary": "Fetch Failed"}

        soup = BeautifulSoup(html, 'html.parser')
        content_div = soup.select_one(selector)
        if not content_div:
            return {"useful": False, "category": "일반", "summary": "Content Not Found"}

        text = content_div.get_text(separator=' ', strip=True)
        if len(text) < 50:
            return {"useful": False, "category": "일반", "summary": "Text too short"}

        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            # JSON output prompt
            prompt = (
                f"Analyze this university notice and return a JSON object.\n"
                f"1. 'useful': boolean. Is this useful for students? (True/False)\n"
                f"2. 'category': string. Choose one: '장학', '학사', '취업', '일반'.\n"
                f"3. 'summary': string. 3 bullet points in Korean, noun-ending (~함).\n"
                f"Content:\n{text[:3000]}"
            )
            # Force JSON response (Gemini 1.5 Flash supports this via prompt engineering usually, 
            # but let's use response_mime_type if available or just parse text)
            # For simplicity and compatibility, we'll ask for JSON string and parse it.
            response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            time.sleep(4)
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            return {"useful": True, "category": "일반", "summary": "AI Error"}

    def send_telegram(self, message: str, topic_id: int = None, is_error: bool = False):
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
        if topic_id:
            payload['message_thread_id'] = topic_id

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

    def process_weekly_briefing(self, target: Dict):
        # Run only on Sunday (0=Monday, 6=Sunday)
        if datetime.datetime.today().weekday() != 6:
            return

        logger.info(f"Processing Weekly Briefing for {target['name']}...")
        html = self.fetch_page(target['url'])
        if not html: return

        # For calendar, we just dump the text to AI
        soup = BeautifulSoup(html, 'html.parser')
        # Try to find the table or content box
        content = soup.select_one('.b-content-box') or soup.select_one('table')
        
        if not content:
            logger.warning("No content found for calendar.")
            return

        text = content.get_text(separator=' ', strip=True)
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            today = datetime.date.today()
            next_week = today + datetime.timedelta(days=7)
            
            prompt = (
                f"Here is the academic calendar list.\n"
                f"Current Date: {today}\n"
                f"Target Period: {today} ~ {next_week}\n\n"
                f"Task: Extract and summarize the schedule for the Target Period.\n"
                f"Output Format: Korean, Bullet points.\n"
                f"If there are no events in this period, say '이번 주 학사 일정은 없습니다.'\n\n"
                f"Content:\n{text[:5000]}"
            )
            
            response = model.generate_content(prompt)
            summary = response.text.strip()
            
            # Send Message
            topic_id = self.config.get('topic_map', {}).get('학사', 0)
            msg = (
                f"📅 *주간 학사 일정 브리핑*\n"
                f"({today} ~ {next_week})\n\n"
                f"{self.escape_markdown_v2(summary)}\n\n"
                f"[전체 일정 보기]({target['url']}) \\#학사 \\#일정"
            )
            self.send_telegram(msg, topic_id=topic_id)
            
        except Exception as e:
            logger.error(f"Weekly Briefing failed: {e}")

    def parse_list(self, html: str, last_id: Optional[str]) -> List[Dict]:
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
                
                # Sticky Post Detection
                # If article_id is None, it might be a sticky post or invalid.
                # Also check the first column for '공지' text or icon
                first_col_text = cols[0].get_text(strip=True)
                if not article_id or '공지' in first_col_text:
                    continue

                if last_id and article_id == last_id:
                    break

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
                # Special handling for Calendar
                if target.get('type') == 'calendar':
                    self.process_weekly_briefing(target)
                    continue

                logger.info(f"Checking {target['name']}...")
                
                last_id = self.get_last_id(target['key'])
                html = self.fetch_page(target['url'])
                
                if not html: continue

                # No keywords passed, we filter by AI later
                new_items = self.parse_list(html, last_id)
                
                if not new_items:
                    logger.info("No new items found.")
                    continue

                for item in reversed(new_items):
                    full_url = urllib.parse.urljoin(target['url'], item['link'])
                    
                    # AI Analysis
                    analysis = self.get_ai_analysis(full_url, target['content_selector'])
                    
                    # Filter if not useful (optional, user said "AI Logic (remove keyword filtering)", 
                    # implying we rely on AI 'useful' flag or just categorize everything.
                    # Let's trust 'useful' flag but default to True if unsure.
                    if not analysis.get('useful', True):
                        logger.info(f"Skipping {item['title']} (AI deemed not useful)")
                        self.update_last_id(target['key'], item['id']) # Mark as seen
                        continue

                    category = analysis.get('category', '일반')
                    summary = analysis.get('summary', '')
                    
                    # Get Topic ID
                    topic_id = self.config.get('topic_map', {}).get(category, 0)
                    
                    # Prepare Message
                    safe_title = self.escape_markdown_v2(item['title'])
                    safe_name = self.escape_markdown_v2(target['name'])
                    safe_cat = self.escape_markdown_v2(category)
                    
                    attach_mark = " 📎[첨부파일]" if item['has_attach'] else ""
                    safe_attach = self.escape_markdown_v2(attach_mark)
                    
                    safe_summary = self.escape_markdown_v2(summary)
                    summary_section = f"\n\n🤖 *AI 요약 ({safe_cat})*\n{safe_summary}"

                    msg = (
                        f"*{safe_name}*\n"
                        f"[{safe_title}]({full_url}){safe_attach}\n"
                        f"{summary_section}\n"
                        f"\\#알림 \\#{safe_cat}"
                    )
                    
                    self.send_telegram(msg, topic_id=topic_id)
                    
                    self.update_last_id(target['key'], item['id'])
                    time.sleep(1)

        except Exception as e:
            error_msg = f"🚨 <b>[에러 발생]</b>\n<pre>{str(e)}</pre>"
            self.send_telegram(error_msg, is_error=True)
            raise

if __name__ == "__main__":
    Scraper = NoticeScraper()
    Scraper.run()
