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
STATE_FILE = 'state.json'
GEMINI_MODEL = 'gemini-2.5-flash'
KST = pytz.timezone('Asia/Seoul')

class NoticeScraper:
    def __init__(self):
        self.config = self._load_config()
        self.state = self._load_state()
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

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _save_state(self):
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

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

    def get_ai_analysis(self, url: str, selector: str, html_content: str = None) -> Dict[str, Any]:
        if not os.environ.get('GEMINI_API_KEY'):
            return {"useful": True, "category": "일반", "summary": "AI Key Missing"}

        if html_content:
            html = html_content
        else:
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
            # Enhanced Prompt with User Persona & Flexible Formatting
            prompt = (
                f"Analyze this university notice for a Computer Engineering student.\n"
                f"1. 'useful': boolean. Is this useful? (True/False)\n"
                f"   - CRITICAL: Mark as TRUE for Scholarships, Jobs, Academic Schedule, Dormitory, and General Campus News.\n"
                f"   - Also mark as TRUE for any Computer Engineering or Software related news.\n"
                f"   - Only mark as FALSE if it is clearly irrelevant (e.g., 'Test Post', 'Arts Dept specific event' with no general interest).\n"
                f"2. 'category': string. Choose one: '장학', '학사', '취업', 'dormitory', '일반'.\n"
                f"3. 'summary': string. Summarize concisely in Korean (Max 3 lines).\n"
                f"   - End sentences with noun-endings (~함).\n"
                f"   - Use structured format ONLY if applicable (e.g., '- 일시: ...', '- 대상: ...').\n"
                f"   - Otherwise, use natural bullet points starting with a hyphen (-).\n"
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

    def pin_message(self, message_id: int):
        if not self.telegram_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/pinChatMessage"
        payload = {'chat_id': self.chat_id, 'message_id': message_id}
        try:
            self.session.post(url, json=payload)
            logger.info(f"Pinned message {message_id}")
        except Exception as e:
            logger.error(f"Failed to pin message: {e}")

    def unpin_message(self, message_id: int):
        if not self.telegram_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/unpinChatMessage"
        payload = {'chat_id': self.chat_id, 'message_id': message_id}
        try:
            self.session.post(url, json=payload)
            logger.info(f"Unpinned message {message_id}")
        except Exception as e:
            logger.error(f"Failed to unpin message: {e}")

    def send_telegram(self, message: str, topic_id: int = None, is_error: bool = False, 
                      buttons: List[Dict] = None, photo_url: str = None, photo_data: bytes = None) -> Optional[int]:
        if not self.telegram_token or not self.chat_id:
            logger.warning("Telegram credentials missing.")
            return None

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
            result = response.json()
            time.sleep(10) # Rate limiting (Safety)
            return result.get('result', {}).get('message_id')
            
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
        return None

    def get_korean_weekday(self, date_obj) -> str:
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        return weekdays[date_obj.weekday()]

    def process_weekly_briefing(self, target: Dict):
        # Run only on Sunday (6) between 18:00 and 19:00
        now_kst = datetime.datetime.now(KST)
        if now_kst.weekday() != 6:
            return
        if not (18 <= now_kst.hour <= 19):
            return

        # Check State to run only once
        today_str = now_kst.strftime('%Y-%m-%d')
        if self.state.get('last_weekly_briefing') == today_str:
            logger.info("Weekly briefing already sent today.")
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
            
            # Update State
            self.state['last_weekly_briefing'] = today_str
            self._save_state()
            
        except Exception as e:
            logger.error(f"Weekly Briefing failed: {e}")

    def process_daily_calendar_check(self, target: Dict):
        # Daily Academic Schedule Check
        # Morning (06-07): Today's Schedule
        # Evening (18-19): Tomorrow's Schedule
        
        now_kst = datetime.datetime.now(KST)
        hour = now_kst.hour
        today_str = now_kst.strftime('%Y-%m-%d')
        
        check_type = None
        target_date = None
        
        if 6 <= hour <= 7:
            check_type = 'morning'
            target_date = now_kst.date()
            state_key = 'last_calendar_check_morning'
        elif 18 <= hour <= 19:
            check_type = 'evening'
            target_date = now_kst.date() + datetime.timedelta(days=1)
            state_key = 'last_calendar_check_evening'
        else:
            return # Not in time window

        if self.state.get(state_key) == today_str:
            logger.info(f"Daily calendar check ({check_type}) already done today.")
            return

        logger.info(f"Processing Daily Calendar Check ({check_type}) for {target_date}...")
        
        html = self.fetch_page(target['url'])
        if not html: return

        soup = BeautifulSoup(html, 'html.parser')
        content = soup.select_one('.b-content-box') or soup.select_one('table')
        if not content: return

        text = content.get_text(separator=' ', strip=True)

        try:
            time.sleep(10)
            model = genai.GenerativeModel(GEMINI_MODEL)
            
            prompt = (
                f"Here is the academic calendar list.\n"
                f"Target Date: {target_date}\n\n"
                f"Task: Extract events happening ON {target_date}.\n"
                f"Output Format: Korean, Bullet points with hyphens (-).\n"
                f"If there are no events on this specific day, explicitly say '일정이 없습니다.'\n\n"
                f"Content:\n{text[:5000]}"
            )
            
            response = model.generate_content(prompt)
            time.sleep(10)
            summary = response.text.strip()
            
            # If AI says "일정이 없습니다" (or similar), we still send it as requested by user
            # "학사 일정 및 안내 모두 그 주나 없는 하루는 일정이 없다고 고지"
            
            topic_id = self.config.get('topic_map', {}).get('학사', 0)
            
            title_prefix = "오늘의" if check_type == 'morning' else "내일의"
            weekday_str = self.get_korean_weekday(target_date)
            
            msg = (
                f"📅 <b>{title_prefix} 학사 일정</b>\n"
                f"({target_date} {weekday_str})\n\n"
                f"{self.escape_html(summary)}\n\n"
                f"<a href='{target['url']}'>[전체 일정 보기]</a> #학사 #일정"
            )
            self.send_telegram(msg, topic_id=topic_id)
            
            # Update State
            self.state[state_key] = today_str
            self._save_state()

        except Exception as e:
            logger.error(f"Daily Calendar Check failed: {e}")

    def process_daily_menu(self, target: Dict):
        # 1. Schedule Check: Run Daily (No restriction)
        now_kst = datetime.datetime.now(KST)
        
        logger.info(f"Processing Weekly Menu for {target['name']}...")
        
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
        if not title_link: return

        title = title_link.get_text(strip=True)
        link = title_link.get('href')
        
        # Extract Article ID for Idempotency
        parsed_url = urllib.parse.urlparse(link)
        qs = urllib.parse.parse_qs(parsed_url.query)
        article_id = qs.get('articleNo', [None])[0]

        if not article_id: return

        # 3. Idempotency Check
        last_sent_id = self.get_last_id(target['key'])
        
        if last_sent_id == article_id:
            logger.info(f"Menu post {article_id} already sent. Skipping.")
            return

        logger.info(f"Found new weekly menu post: {title} ({article_id})")

        # 4. Fetch Detail Page
        full_url = urllib.parse.urljoin(target['url'], link)
        detail_html = self.fetch_page(full_url)
        if not detail_html: return

        detail_soup = BeautifulSoup(detail_html, 'html.parser')
        content = detail_soup.select_one('.b-content-box')
        
        if not content: return

        # 5. Extract Image and Download
        image_url = None
        img_tag = content.find('img')
        if img_tag:
            src = img_tag.get('src', '')
            if src:
                image_url = urllib.parse.urljoin(target['url'], src)

        topic_id = self.config.get('topic_map', {}).get('dormitory', 0)
        safe_title = self.escape_html(title)
        
        # Add Day of Week to Header
        # Note: The title usually contains the date range, but we add the current day for context?
        # User request: "다음 식단표부터는 연도월일 다음 괄호로 무슨 요일인지도 표기할 것."
        # Since this is the *weekly* menu post, the "date" is usually a range.
        # However, the user might mean the *notification date* or the *menu date*.
        # Given it's a "Weekly Menu" post, I will add the *current* day of week to the notification header
        # to indicate when this notification was sent/detected.
        # OR, if the user means the *content* of the menu, I can't change the image.
        # I will assume they mean the Telegram Message Header.
        
        today_str = now_kst.strftime('%Y-%m-%d')
        weekday_str = self.get_korean_weekday(now_kst)
        
        if image_url:
            logger.info(f"Found menu image: {image_url}")
            try:
                # Download image bytes with Referer
                headers = {'Referer': full_url}
                img_response = self.session.get(image_url, headers=headers)
                img_response.raise_for_status()
                img_data = img_response.content
                
                msg = (
                    f"🍚 <b>{safe_title}</b>\n"
                    f"({today_str} {weekday_str})\n\n"
                    f"<a href='{full_url}'>[식단 게시판 바로가기]</a>"
                )
                # Send with bytes
                msg_id = self.send_telegram(msg, topic_id=topic_id, photo_data=img_data)
                
                if msg_id:
                    self.update_last_id(target['key'], article_id)
                    
                    # Pinning Logic
                    old_pin_id = self.state.get('last_pinned_menu_id')
                    if old_pin_id:
                        self.unpin_message(old_pin_id)
                    
                    self.pin_message(msg_id)
                    self.state['last_pinned_menu_id'] = msg_id
                    self._save_state()

            except Exception as e:
                logger.error(f"Failed to download/send menu image: {e}")
        else:
            logger.warning("No menu image found in the post.")

    def parse_list(self, html: str, last_id: Optional[str], current_page_url: str) -> List[Dict]:
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

                # Extract Attachments for Buttons (Strict Deduplication)
                attachments = []
                seen_urls = set()
                file_links = row.select('a[href*="fileDownload"]')
                
                for fl in file_links:
                    f_name = fl.get_text(strip=True) or "첨부파일"
                    # Use current_page_url for robust relative link resolution
                    f_url = urllib.parse.urljoin(current_page_url, fl.get('href'))
                    
                    if f_url in seen_urls:
                        continue
                    
                    seen_urls.add(f_url)
                    attachments.append({"text": f"📄 {f_name}", "url": f_url})

                # Check for Image (List View)
                image_url = None
                imgs = row.find_all('img')
                for img in imgs:
                    src = img.get('src', '')
                    if 'file' not in src.lower() and 'icon' not in src.lower():
                        image_url = urllib.parse.urljoin(current_page_url, src)
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
        logger.info("🚀 SCRAPER VERSION: 2025-11-24 UPDATE 11 (DAILY ACADEMIC + WEEKLY BRIEF + FIXES)")
        processed_ids = set() # Deduplication set for this run

        try:
            for target in self.config['targets']:
                # Special handling
                t_type = target.get('type')
                if t_type == 'calendar':
                    self.process_weekly_briefing(target)
                    self.process_daily_calendar_check(target)
                    continue
                elif t_type == 'menu':
                    self.process_daily_menu(target)
                    continue

                logger.info(f"Checking {target['name']}...")
                
                last_id = self.get_last_id(target['key'])
                html = self.fetch_page(target['url'])
                
                if not html: continue

                # Pass target['url'] (current page) as base for parse_list
                new_items = self.parse_list(html, last_id, target['url'])
                
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
                    
                    # Fetch Detail Page for AI & Image Extraction
                    detail_html = self.fetch_page(full_url)
                    
                    # Extract Image from Content if not in List
                    final_image_data = None
                    final_image_url = item['image_url']
                    
                    if detail_html:
                        detail_soup = BeautifulSoup(detail_html, 'html.parser')
                        content_div = detail_soup.select_one(target['content_selector'])
                        if content_div:
                            # If no image from list, try content
                            if not final_image_url:
                                img_tag = content_div.find('img')
                                if img_tag:
                                    src = img_tag.get('src', '')
                                    if src and 'file' not in src.lower() and 'icon' not in src.lower():
                                        final_image_url = urllib.parse.urljoin(target['base_url'], src)
                    
                    # Download Image if exists
                    if final_image_url:
                        try:
                            headers = {'Referer': full_url}
                            img_resp = self.session.get(final_image_url, headers=headers)
                            img_resp.raise_for_status()
                            final_image_data = img_resp.content
                        except Exception as e:
                            logger.error(f"Failed to download image {final_image_url}: {e}")

                    # AI Analysis (Reuse detail_html)
                    analysis = self.get_ai_analysis(full_url, target['content_selector'], html_content=detail_html)
                    
                    if not analysis.get('useful', True):
                        logger.info(f"Skipping {item['title']} (AI deemed not useful)")
                        self.update_last_id(target['key'], item['id'])
                        continue

                    category = analysis.get('category', '일반')
                    summary = analysis.get('summary', '')
                    
                    if isinstance(summary, list):
                        summary = '\n'.join(summary)
                    
                    topic_id = self.config.get('topic_map', {}).get(category, 0)
                    
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
                        final_buttons.append({
                            "text": f"📂 첨부파일 {len(item['attachments'])}개 보기",
                            "url": full_url
                        })
                    else:
                        for btn in item['attachments']:
                            final_buttons.append(btn)

                    self.send_telegram(
                        msg, 
                        topic_id=topic_id, 
                        buttons=final_buttons,
                        photo_data=final_image_data # Send bytes
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
