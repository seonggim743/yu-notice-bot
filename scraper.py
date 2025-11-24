import os
import json
import time
import datetime
import logging
import requests
import pytz
from bs4 import BeautifulSoup
import google.generativeai as genai
from supabase import create_client, Client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import List, Dict, Optional, Any
import urllib.parse

# --- Logging Configuration ---
# Use KST for logging
class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.astimezone(pytz.timezone('Asia/Seoul'))

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            try:
                s = dt.isoformat(timespec='milliseconds')
            except TypeError:
                s = dt.isoformat()
        return s

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.handlers = []
logger.addHandler(handler)
logger.propagate = False

# --- Constants ---
CONFIG_FILE = 'config.json'
GEMINI_MODEL = 'gemini-2.5-flash'
KST = pytz.timezone('Asia/Seoul')

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
            time.sleep(10) # Rate limiting (Safety)
            model = genai.GenerativeModel(GEMINI_MODEL)
            # Enhanced Prompt with User Persona
            prompt = (
                f"Analyze this university notice for a Computer Engineering student.\n"
                f"1. 'useful': boolean. Is this useful? (True/False)\n"
                f"   - CRITICAL: Mark as TRUE for Scholarships, Jobs, Academic Schedule, Dormitory, and General Campus News.\n"
                f"   - Also mark as TRUE for any Computer Engineering or Software related news.\n"
                f"   - Only mark as FALSE if it is clearly irrelevant (e.g., 'Test Post', 'Arts Dept specific event' with no general interest).\n"
                f"2. 'category': string. Choose one: '장학', '학사', '취업', 'dormitory', '일반'.\n"
                f"3. 'summary': string. 3 bullet points in Korean. MUST start each line with a hyphen (-).\n"
                f"Content:\n{text[:4000]}"
            )
            
            response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
            time.sleep(10) # Rate limiting (Safety)
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            # Fallback: Return useful=True so it gets sent, but empty summary
            return {"useful": True, "category": "일반", "summary": ""}

    def escape_html(self, text: str) -> str:
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def send_telegram(self, message: str, topic_id: int = None, is_error: bool = False, 
                      buttons: List[Dict] = None, photo_url: str = None, photo_data: bytes = None):
        if not self.telegram_token or not self.chat_id:
            logger.warning("Telegram credentials missing.")
            return

        # Endpoint selection
        endpoint = "sendMessage"
        if photo_url or photo_data:
            endpoint = "sendPhoto"

        url = f"https://api.telegram.org/bot{self.telegram_token}/{endpoint}"
        
        # Base Payload
        payload = {
            'chat_id': self.chat_id,
            'parse_mode': 'HTML',
        }
        
        if topic_id:
            payload['message_thread_id'] = topic_id

        if buttons:
            inline_keyboard = []
            for btn in buttons:
                inline_keyboard.append([{
                    "text": btn['text'],
                    "url": btn['url']
                }])
            payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})

        # Handle Photo (URL vs Bytes)
        files = None
        if photo_data:
            files = {'photo': photo_data}
            payload['caption'] = message
        elif photo_url:
            payload['photo'] = photo_url
            payload['caption'] = message
        else:
            payload['text'] = message
            payload['disable_web_page_preview'] = True

        try:
            if files:
                # When using files, payload must be sent as 'data', not 'json'
                response = self.session.post(url, data=payload, files=files)
            else:
                response = self.session.post(url, json=payload)
                
            response.raise_for_status()
            time.sleep(10) # Rate limiting (Safety)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error("TELEGRAM_TOKEN이 올바른지 확인하세요 (404 Not Found).")
            elif e.response.status_code == 400:
                logger.error(f"Telegram 400 Error (Bad Request): {e.response.text}")
                if not files:
                    logger.error(f"Actual Request Body: {e.request.body}")
                
                # Smart Fallback: Retry with Plain Text (KEEP TOPIC ID)
                if not is_error:
                    logger.info("Attempting Smart Fallback (Plain Text) [HTML FAILED]...")
                    payload['parse_mode'] = None
                    try:
                        if files:
                            self.session.post(url, data=payload, files=files)
                        else:
                            self.session.post(url, json=payload)
                        logger.info("Fallback message sent successfully.")
                        time.sleep(10)
                    except Exception as fallback_e:
                        logger.error(f"Fallback failed: {fallback_e}")
            else:
                logger.error(f"Failed to send Telegram message: {e}")

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def process_weekly_briefing(self, target: Dict):
        # Run only on Sunday (0=Monday, 6=Sunday)
        now_kst = datetime.datetime.now(KST)
        if now_kst.weekday() != 6:
            return

        logger.info(f"Processing Weekly Briefing for {target['name']}...")
        html = self.fetch_page(target['url'])
        if not html: return

        soup = BeautifulSoup(html, 'html.parser')
        content = soup.select_one('.b-content-box') or soup.select_one('table')
        
        if not content:
            logger.warning("No content found for calendar.")
            return

        text = content.get_text(separator=' ', strip=True)
        
        try:
            time.sleep(10)
            model = genai.GenerativeModel(GEMINI_MODEL)
            today = now_kst.date()
            next_week = today + datetime.timedelta(days=7)
            
            prompt = (
                f"Here is the academic calendar list.\n"
                f"Current Date: {today}\n"
                f"Target Period: {today} ~ {next_week}\n\n"
                f"Task: Extract and summarize the schedule for the Target Period.\n"
                f"Output Format: Korean, Bullet points with hyphens (-).\n"
                f"If there are no events in this period, say '이번 주 학사 일정은 없습니다.'\n\n"
                f"Content:\n{text[:5000]}"
            )
            
            response = model.generate_content(prompt)
            time.sleep(10)
            summary = response.text.strip()
            
            topic_id = self.config.get('topic_map', {}).get('학사', 0)
            msg = (
                f"📅 <b>주간 학사 일정 브리핑</b>\n"
                f"({today} ~ {next_week})\n\n"
                f"{self.escape_html(summary)}\n\n"
                f"<a href='{target['url']}'>[전체 일정 보기]</a> #학사 #일정"
            )
            self.send_telegram(msg, topic_id=topic_id)
            
        except Exception as e:
            logger.error(f"Weekly Briefing failed: {e}")

    def process_daily_menu(self, target: Dict):
        # 1. Start Time Check: 07:00 ~ 23:00 KST
        now_kst = datetime.datetime.now(KST)
        if not (7 <= now_kst.hour <= 23):
            logger.info(f"Skipping Daily Menu (Current time {now_kst.hour}h is outside 07-23h window)")
            return

        logger.info(f"Processing Daily Menu for {target['name']}...")
        
        # 2. Fetch List Page
        html = self.fetch_page(target['url'])
        if not html: return

        soup = BeautifulSoup(html, 'html.parser')
        # Find the first post in the table
        first_row = soup.select_one('table tbody tr')
        if not first_row:
            logger.warning("No rows found in menu board.")
            return

        title_link = first_row.select_one('a')
        if not title_link:
            return

        title = title_link.get_text(strip=True)
        link = title_link.get('href')
        
        # Extract Article ID for Idempotency
        parsed_url = urllib.parse.urlparse(link)
        qs = urllib.parse.parse_qs(parsed_url.query)
        article_id = qs.get('articleNo', [None])[0]

        if not article_id:
            logger.warning("Could not extract articleNo from menu link.")
            return

        # 3. Idempotency Check
        last_sent_id = self.get_last_id(target['key'])
        if last_sent_id == article_id:
            logger.info(f"Menu post {article_id} already sent. Skipping.")
            return

        logger.info(f"Found new menu post: {title} ({article_id})")

        # 4. Fetch Detail Page
        full_url = urllib.parse.urljoin(target['url'], link)
        detail_html = self.fetch_page(full_url)
        if not detail_html: return

        detail_soup = BeautifulSoup(detail_html, 'html.parser')
        content = detail_soup.select_one('.b-content-box')
        
        if not content:
            logger.warning("No content found in menu detail page.")
            return

        # 5. Extract Image and Download
        image_url = None
        img_tag = content.find('img')
        if img_tag:
            src = img_tag.get('src', '')
            if src:
                image_url = urllib.parse.urljoin(target['url'], src)

        display_date = now_kst.strftime("%Y-%m-%d")
        topic_id = self.config.get('topic_map', {}).get('dormitory', 0)

        if image_url:
            logger.info(f"Found menu image: {image_url}")
            try:
                # Download image bytes
                img_response = self.session.get(image_url)
                img_response.raise_for_status()
                img_data = img_response.content
                
                msg = (
                    f"🍚 <b>기숙사 식단표</b> ({display_date})\n\n"
                    f"<a href='{full_url}'>[식단 게시판 바로가기]</a>"
                )
                # Send with bytes
                self.send_telegram(msg, topic_id=topic_id, photo_data=img_data)
                self.update_last_id(target['key'], article_id)
            except Exception as e:
                logger.error(f"Failed to download/send menu image: {e}")
        else:
            logger.warning("No menu image found in the post.")

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
                first_col_text = cols[0].get_text(strip=True)
                if not article_id or '공지' in first_col_text:
                    continue

                if last_id and article_id == last_id:
                    break

                # Extract Attachments for Buttons
                attachments = []
                file_links = row.select('a[href*="fileDownload"]')
                for fl in file_links:
                    f_name = fl.get_text(strip=True) or "첨부파일"
                    # Fix: Ensure correct base URL for attachments
                    f_url = urllib.parse.urljoin("https://hcms.yu.ac.kr", fl.get('href'))
                    attachments.append({"text": f"📄 {f_name}", "url": f_url})

                # Check for Image
                image_url = None
                imgs = row.find_all('img')
                for img in imgs:
                    src = img.get('src', '')
                    if 'file' not in src.lower() and 'icon' not in src.lower():
                        image_url = urllib.parse.urljoin("https://hcms.yu.ac.kr", src)
                        break

                new_items.append({
                    'id': article_id,
                    'title': title,
                    'link': link,
                    'attachments': attachments,
                    'image_url': image_url
                })
            except Exception as e:
                logger.error(f"Error parsing row: {e}")
                continue
        
        return new_items

    def run(self):
        logger.info("🚀 SCRAPER VERSION: 2025-11-24 UPDATE 7 (AI REFINE + ATTACH GROUP + IMG UPLOAD)")
        processed_ids = set() # Deduplication set for this run

        try:
            for target in self.config['targets']:
                # Special handling
                t_type = target.get('type')
                if t_type == 'calendar':
                    self.process_weekly_briefing(target)
                    continue
                elif t_type == 'menu':
                    self.process_daily_menu(target)
                    continue

                logger.info(f"Checking {target['name']}...")
                
                last_id = self.get_last_id(target['key'])
                html = self.fetch_page(target['url'])
                
                if not html: continue

                new_items = self.parse_list(html, last_id)
                
                if not new_items:
                    logger.info("No new items found.")
                    continue

                for item in reversed(new_items):
                    # Deduplication Check
                    unique_key = f"{target['key']}_{item['id']}"
                    if unique_key in processed_ids:
                        logger.info(f"Skipping duplicate item: {item['title']}")
                        continue
                    processed_ids.add(unique_key)

                    full_url = urllib.parse.urljoin(target['url'], item['link'])
                    
                    # AI Analysis
                    analysis = self.get_ai_analysis(full_url, target['content_selector'])
                    
                    if not analysis.get('useful', True):
                        logger.info(f"Skipping {item['title']} (AI deemed not useful)")
                        self.update_last_id(target['key'], item['id'])
                        continue

                    category = analysis.get('category', '일반')
                    summary = analysis.get('summary', '')
                    
                    # Fix: Handle list summary
                    if isinstance(summary, list):
                        summary = '\n'.join(summary)
                    
                    # Get Topic ID
                    topic_id = self.config.get('topic_map', {}).get(category, 0)
                    
                    # Prepare Message (HTML)
                    safe_title = self.escape_html(item['title'])
                    safe_name = self.escape_html(target['name'])
                    safe_cat = self.escape_html(category)
                    safe_summary = self.escape_html(str(summary))
                    
                    if summary:
                        summary_section = f"\n\n🤖 <b>AI 요약 ({safe_cat})</b>\n{safe_summary}"
                    else:
                        summary_section = ""

                    msg = (
                        f"<b>{safe_name}</b>\n"
                        f"<a href='{full_url}'>{safe_title}</a>\n"
                        f"{summary_section}\n"
                        f"#알림 #{safe_cat}"
                    )
                    
                    # Fix attachment URLs & Grouping
                    final_buttons = []
                    if len(item['attachments']) > 2:
                        # Group attachments
                        final_buttons.append({
                            "text": f"📂 첨부파일 {len(item['attachments'])}개 보기",
                            "url": full_url
                        })
                    else:
                        # Show individual buttons
                        for btn in item['attachments']:
                            if btn['url'].startswith('/'):
                                btn['url'] = urllib.parse.urljoin(target['base_url'], btn['url'])
                            final_buttons.append(btn)

                    self.send_telegram(
                        msg, 
                        topic_id=topic_id, 
                        buttons=final_buttons,
                        photo_url=item['image_url']
                    )
                    
                    self.update_last_id(target['key'], item['id'])
                    time.sleep(10) # Rate limiting (Safety)

        except Exception as e:
            error_msg = f"🚨 <b>[에러 발생]</b>\n<pre>{str(e)}</pre>"
            self.send_telegram(error_msg, is_error=True)
            raise

if __name__ == "__main__":
    Scraper = NoticeScraper()
    Scraper.run()
