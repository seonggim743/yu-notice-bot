import os
import json
import time
import datetime
import logging
import asyncio
import aiohttp
import pytz
import re
import hashlib
import sys
from bs4 import BeautifulSoup
import google.generativeai as genai
from supabase import create_client, Client
from typing import List, Dict, Optional, Any
import urllib.parse
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from models import BotConfig, NoticeItem, ScraperState, TargetConfig, Attachment

# --- Configuration ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# KST Timezone
KST = pytz.timezone('Asia/Seoul')

class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.astimezone(KST)

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec='milliseconds')

handler = logging.StreamHandler()
handler.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.handlers = []
logger.addHandler(handler)
logger.propagate = False

CONFIG_FILE = 'config.json'
GEMINI_MODEL = 'gemini-2.5-flash'

class NoticeScraper:
    def __init__(self):
        self.config = self._load_config()
        self.supabase = self._init_supabase()
        self.state = self._load_state()
        self._init_gemini()
        
        self.telegram_token = os.environ.get('TELEGRAM_TOKEN')
        self.chat_id = os.environ.get('CHAT_ID')

    def _load_config(self) -> BotConfig:
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return BotConfig(**data)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def _init_supabase(self) -> Optional[Client]:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            logger.warning("Supabase credentials missing. State persistence will be disabled.")
            return None
        return create_client(url, key)

    def _load_state(self) -> ScraperState:
        if not self.supabase:
            # Fail-Safe: If in CI/CD and Supabase is missing, ABORT to prevent spam.
            if os.environ.get('CI'):
                logger.critical("Supabase connection failed in CI environment. Aborting to prevent duplicate alerts.")
                sys.exit(1)
            return ScraperState()
            
        try:
            response = self.supabase.table('crawling_logs').select('*').execute()
            state_data = {}
            for row in response.data:
                if row['site_name'].startswith('STATE_'):
                    key = row['site_name'].replace('STATE_', '', 1)
                    try:
                        state_data[key] = json.loads(row['last_post_id'])
                    except:
                        state_data[key] = row['last_post_id']
            return ScraperState(**state_data)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            if os.environ.get('CI'):
                logger.critical("Failed to load state in CI. Aborting.")
                sys.exit(1)
            return ScraperState()

    def _save_state(self):
        if not self.supabase: return
        try:
            state_dict = self.state.model_dump()
            for key, value in state_dict.items():
                val_str = json.dumps(value, ensure_ascii=False, default=str)
                data = {'site_name': f"STATE_{key}", 'last_post_id': val_str}
                self.supabase.table('crawling_logs').upsert(data).execute()
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _init_gemini(self):
        api_key = os.environ.get('GEMINI_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
        else:
            logger.warning("Gemini API Key missing. AI Summary will be disabled.")

    def get_last_id(self, site_key: str) -> Optional[str]:
        # Legacy method kept for compatibility, but CDC uses 'notices' table now.
        if not self.supabase: return None
        try:
            response = self.supabase.table('crawling_logs').select('last_post_id').eq('site_name', site_key).execute()
            if response.data:
                return response.data[0]['last_post_id']
        except Exception as e:
            logger.error(f"Failed to fetch last ID for {site_key}: {e}")
        return None

    def update_last_id(self, site_key: str, new_id: str):
        if not self.supabase: return
        try:
            data = {'site_name': site_key, 'last_post_id': new_id}
            self.supabase.table('crawling_logs').upsert(data).execute()
        except Exception as e:
            logger.error(f"Failed to update last ID for {site_key}: {e}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_page(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        try:
            async with session.get(url, timeout=15) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            raise # Re-raise for tenacity

    async def get_ai_analysis(self, text: str, prompt_template: str) -> Dict[str, Any]:
        if not os.environ.get('GEMINI_API_KEY'):
            return {"useful": True, "category": "일반", "summary": "AI Key Missing"}

        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = prompt_template.format(text=text[:4000])
            
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: model.generate_content(prompt, generation_config={"response_mime_type": "application/json"}))
            
            # Token Tracking
            try:
                usage = response.usage_metadata
                self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
            except Exception as e:
                logger.error(f"Failed to save token usage: {e}")

            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            return {"useful": True, "category": "일반", "summary": ""}

    def _save_token_usage(self, prompt_tokens: int, completion_tokens: int):
        if not self.supabase: return
        try:
            data = {
                'timestamp': datetime.datetime.now(KST).isoformat(),
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': prompt_tokens + completion_tokens,
                'model': GEMINI_MODEL
            }
            self.supabase.table('token_usage').insert(data).execute()
        except Exception as e:
            logger.error(f"Token usage insert failed: {e}")

    def escape_html(self, text: str) -> str:
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def calculate_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def generate_calendar_url(self, title: str, date_str: str) -> str:
        """Generates a Google Calendar Add URL."""
        try:
            # Assume date_str is YYYY-MM-DD
            dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
            # Set as all-day event (YYYYMMDD / YYYYMMDD+1)
            start = dt.strftime('%Y%m%d')
            end = (dt + datetime.timedelta(days=1)).strftime('%Y%m%d')
            
            params = {
                'action': 'TEMPLATE',
                'text': title,
                'dates': f"{start}/{end}",
                'details': 'Added by Yu Notice Bot'
            }
            return f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
        except:
            return ""

    async def send_health_check(self, session: aiohttp.ClientSession):
        """Sends a daily health check message to the admin/dev channel."""
        now = datetime.datetime.now(KST)
        
        # Token Stats
        token_msg = "Token Usage: N/A"
        if self.supabase:
            try:
                yesterday = (now - datetime.timedelta(days=1)).date()
                today = now.date()
                
                # Yesterday's usage
                start = datetime.datetime.combine(yesterday, datetime.time.min).isoformat()
                end = datetime.datetime.combine(today, datetime.time.min).isoformat()
                
                resp_yesterday = self.supabase.table('token_usage').select('total_tokens').gte('timestamp', start).lt('timestamp', end).execute()
                total_yesterday = sum(row['total_tokens'] for row in resp_yesterday.data)
                
                # Total usage (All time)
                resp_total = self.supabase.table('token_usage').select('total_tokens').execute()
                total_all = sum(row['total_tokens'] for row in resp_total.data)
                
                token_msg = f"Token Usage:\n- Yesterday: {total_yesterday}\n- Total: {total_all}"
            except Exception as e:
                logger.error(f"Failed to fetch token stats: {e}")

        msg = (
            f"✅ <b>System Health Check</b>\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Status: Online\n"
            f"Supabase: {'Connected' if self.supabase else 'Disconnected'}\n"
            f"{token_msg}"
        )
        await self.send_telegram(session, msg, self.config.topic_map.get('일반'))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def send_telegram(self, session: aiohttp.ClientSession, message: str, topic_id: int = None, 
                            buttons: List[Dict] = None, photo_data: bytes = None) -> Optional[int]:
        if not self.telegram_token or not self.chat_id: return None

        endpoint = "sendPhoto" if photo_data else "sendMessage"
        url = f"https://api.telegram.org/bot{self.telegram_token}/{endpoint}"
        
        payload = {'chat_id': self.chat_id, 'parse_mode': 'HTML'}
        if topic_id: payload['message_thread_id'] = topic_id
        
        if buttons:
            inline_keyboard = [[{"text": b['text'], "url": b['url']}] for b in buttons]
            payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})

        data = aiohttp.FormData()
        for k, v in payload.items():
            data.add_field(k, str(v))
        
        if photo_data:
            data.add_field('photo', photo_data, filename='image.jpg')
            data.add_field('caption', message)
        else:
            data.add_field('text', message)
            data.add_field('disable_web_page_preview', 'true')

        try:
            async with session.post(url, data=data) as resp:
                resp.raise_for_status()
                result = await resp.json()
                return result.get('result', {}).get('message_id')
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            raise # Re-raise for tenacity

    async def pin_message(self, session: aiohttp.ClientSession, message_id: int):
        if not self.telegram_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/pinChatMessage"
        payload = {'chat_id': self.chat_id, 'message_id': message_id}
        try:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
        except Exception as e:
            logger.error(f"Pin failed: {e}")

    async def unpin_message(self, session: aiohttp.ClientSession, message_id: int):
        if not self.telegram_token or not self.chat_id: return
        url = f"https://api.telegram.org/bot{self.telegram_token}/unpinChatMessage"
        payload = {'chat_id': self.chat_id, 'message_id': message_id}
        try:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
        except Exception as e:
            logger.error(f"Unpin failed: {e}")

    def parse_list(self, html: str, last_id: Optional[str], current_page_url: str) -> List[NoticeItem]:
        soup = BeautifulSoup(html, 'html.parser')
        items = []
        rows = soup.select('table tbody tr')

        for row in rows:
            try:
                cols = row.find_all('td')
                if not cols: continue
                
                title_link = row.select_one('a')
                if not title_link: continue

                title = title_link.get_text(strip=True)
                
                # Title Cleaning: Remove index number but keep Year (e.g. 2025...)
                # Logic: Handle cases like "17 2025..." or "172025..." (concatenated)
                match = re.match(r'^(\d+)\s*(.*)', title)
                if match:
                    num_str = match.group(1)
                    rest = match.group(2)
                    try:
                        num_val = int(num_str)
                        if 2000 <= num_val <= 2100:
                            pass # It's a year, keep it
                        elif num_val > 2100:
                            # Check if it ends with a year (e.g. 172025)
                            year_match = re.search(r'(20\d\d)$', num_str)
                            if year_match:
                                # If rest starts with '학년' or '년', don't add space
                                sep = "" if rest.startswith("학년") or rest.startswith("년") else " "
                                title = year_match.group(1) + sep + rest
                            else:
                                title = rest
                        else:
                            # Small number (index), remove it
                            title = rest
                    except:
                        pass # Keep original if parsing fails

                link = title_link.get('href')
                
                parsed_url = urllib.parse.urlparse(link)
                qs = urllib.parse.parse_qs(parsed_url.query)
                article_id = qs.get('articleNo', [None])[0]
                
                if not article_id: continue

                # Sticky Logic
                is_sticky = 'b-top-box' in row.get('class', []) or '공지' in cols[0].get_text()
                
                # Attachments
                attachments = []
                seen_urls = set()
                for fl in row.select('a[href*="fileDownload"]'):
                    f_url = urllib.parse.urljoin(current_page_url, fl.get('href'))
                    if f_url not in seen_urls:
                        seen_urls.add(f_url)
                        attachments.append(Attachment(text=f"📄 {fl.get_text(strip=True) or '첨부'}", url=f_url))

                # Image
                image_url = None
                for img in row.find_all('img'):
                    src = img.get('src', '')
                    if 'file' not in src.lower() and 'icon' not in src.lower():
                        image_url = urllib.parse.urljoin(current_page_url, src)
                        break

                items.append(NoticeItem(
                    id=article_id,
                    title=title,
                    link=link,
                    attachments=attachments,
                    image_url=image_url
                ))
            except Exception as e:
                logger.error(f"Parse error: {e}")
                continue
        
        # Return ALL items from the first page for CDC check
        return items

    async def process_target(self, session: aiohttp.ClientSession, target: TargetConfig):
        logger.info(f"Checking {target.name}...")
        
        html = await self.fetch_page(session, target.url)
        if not html: return

        # Parse ALL items on the first page
        items = self.parse_list(html, None, target.url)
        if not items: return

        for item in items:
            full_url = urllib.parse.urljoin(target.url, item.link)
            
            # CDC: Check if exists in DB
            db_record = None
            if self.supabase:
                try:
                    resp = self.supabase.table('notices').select('*').eq('site_key', target.key).eq('article_id', item.id).execute()
                    if resp.data:
                        db_record = resp.data[0]
                except Exception as e:
                    logger.error(f"DB Check failed: {e}")

            # Optimization: If exists and we assume no change (skip detail fetch if not strictly needed)
            # But for "Portfolio", we want to check content hash.
            # To avoid fetching 20 pages every time, we can check if 'title' changed in list view first?
            # Or just rely on the fact that we only process "New" or "Modified".
            # Let's fetch detail to be sure (Robustness > Speed for 20 items).
            
            detail_html = await self.fetch_page(session, full_url)
            if not detail_html: continue

            # Extract Content for Hash
            soup = BeautifulSoup(detail_html, 'html.parser')
            content_div = soup.select_one(target.content_selector)
            content_text = content_div.get_text(separator=' ', strip=True) if content_div else ""
            
            # Calculate Hash (Title + Content)
            current_hash = self.calculate_hash(item.title + content_text)
            
            is_new = db_record is None
            is_modified = db_record and db_record.get('content_hash') != current_hash
            
            if not is_new and not is_modified:
                continue # No change, skip

            logger.info(f"Found {'New' if is_new else 'Modified'} item: {item.title}")

            # Logic for Image, AI, etc. (Same as before)
            final_image_url = item.image_url
            if not final_image_url and content_div:
                img = content_div.find('img')
                if img:
                    src = img.get('src', '')
                    if src and 'file' not in src.lower():
                        final_image_url = urllib.parse.urljoin(target.base_url, src)

            photo_data = None
            if final_image_url:
                try:
                    async with session.get(final_image_url, headers={'Referer': full_url}) as resp:
                        if resp.status == 200: photo_data = await resp.read()
                except: pass

            analysis = await self.get_ai_analysis(content_text, self.config.ai_prompt_template)
            
            # Archiving (Save to DB)
            if self.supabase:
                try:
                    db_data = {
                        'site_key': target.key,
                        'article_id': item.id,
                        'title': item.title,
                        'url': full_url,
                        'category': analysis.get('category', '일반'),
                        'content_hash': current_hash,
                        'summary': str(analysis.get('summary', '')),
                        'is_useful': analysis.get('useful', True),
                        'updated_at': datetime.datetime.now(KST).isoformat()
                    }
                    self.supabase.table('notices').upsert(db_data, on_conflict='site_key,article_id').execute()
                except Exception as e:
                    logger.error(f"Archiving failed: {e}")

            if not analysis.get('useful', True):
                continue

            # Send Notification
            category = analysis.get('category', '일반')
            summary = analysis.get('summary', '')
            if isinstance(summary, list): summary = '\n'.join(summary)
            
            is_exam = any(k in item.title for k in ["시험", "중간고사", "기말고사", "강의평가"])
            if is_exam:
                category = "시험/학사"
                analysis['end_date'] = analysis.get('end_date')

            topic_id = self.config.topic_map.get(category, 0)
            if is_exam and topic_id == 0: topic_id = self.config.topic_map.get('학사', 4)
            
            # Force Dormitory Topic
            if target.key == 'dorm_notice':
                topic_id = self.config.topic_map.get('dormitory', 15)

            safe_title = self.escape_html(item.title)
            safe_summary = self.escape_html(str(summary))
            
            prefix = "🆕 " if is_new else "🔄 "
            
            modified_text = "\n\n(수정됨)" if is_modified else ""

            msg = (
                f"{prefix}<b>{self.escape_html(target.name)}</b>\n"
                f"<a href='{full_url}'>{safe_title}</a>\n"
                f"\n🤖 <b>AI 요약 ({category})</b>\n{safe_summary}\n{modified_text}\n"
                f"#알림 #{category}"
            )

            buttons = []
            
            # UX: Add Calendar Button
            if analysis.get('end_date'):
                cal_url = self.generate_calendar_url(item.title, analysis['end_date'])
                if cal_url:
                    buttons.append({"text": "📅 캘린더 등록", "url": cal_url})

            # UX: Attachment Preview / Download
            preview_url = None
            
            # 1. Try to find native preview link (view.jsp or similar)
            if detail_html:
                soup_detail = BeautifulSoup(detail_html, 'html.parser')
                # Look for view.jsp or common preview patterns
                preview_node = soup_detail.find('a', href=re.compile('view\.jsp', re.I)) or \
                               soup_detail.find('a', string=re.compile('미리보기')) or \
                               soup_detail.find('a', class_=re.compile('preview', re.I))
                
                if preview_node and preview_node.get('href'):
                    if 'javascript' not in preview_node.get('href'):
                        preview_url = urllib.parse.urljoin(full_url, preview_node.get('href'))

            if preview_url:
                 buttons.append({"text": "🔍 첨부파일 미리보기", "url": preview_url})
            else:
                # Fallback: Download Links
                if item.attachments:
                    # Show all download links
                    for att in item.attachments:
                         # Shorten filename if too long
                         fname = att.text.replace('📄 ', '')
                         if len(fname) > 20: fname = fname[:17] + "..."
                         buttons.append({"text": f"📥 {fname}", "url": att.url})

            msg_id = await self.send_telegram(session, msg, topic_id, buttons, photo_data)

            if msg_id:
                today = datetime.datetime.now(KST).strftime('%Y-%m-%d')

                if not self.state.daily_notices_buffer:
                    self.state.daily_notices_buffer = {}
                
                if self.state.daily_notices_buffer.get('date') != today:
                    self.state.daily_notices_buffer = {'date': today, 'items': []}
                self.state.daily_notices_buffer['items'].append(f"[{category}] <a href='{full_url}'>{safe_title}</a>")
                self._save_state()

                if is_exam and analysis.get('end_date'):
                    await self.pin_message(session, msg_id)
                    self.state.pinned_exams.append({'message_id': msg_id, 'end_date': analysis['end_date']})
                    self._save_state()

            # Update Legacy Last ID for backward compatibility (optional)
            self.update_last_id(target.key, item.id)
            await asyncio.sleep(2)

    async def process_calendar(self, session: aiohttp.ClientSession, target: TargetConfig):
        # 1. Weekly Briefing (Sunday 18:00+)
        now = datetime.datetime.now(KST)
        today_str = now.strftime('%Y-%m-%d')
        
        if now.weekday() == 6 and now.hour >= 18:
            if self.state.last_weekly_briefing != today_str:
                html = await self.fetch_page(session, target.url)
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    text = soup.get_text(strip=True)
                    
                    start_date = now.date()
                    end_date = now.date() + datetime.timedelta(days=7)
                    prompt = (
                        f"Summarize weekly schedule for {start_date} ~ {end_date}.\n"
                        f"Content: {text[:4000]}\n"
                        f"Output Format: 'MM/DD (Day): Event' (Korean)"
                    )
                    
                    try:
                        model = genai.GenerativeModel(GEMINI_MODEL)
                        loop = asyncio.get_running_loop()
                        response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
                        
                        # Token Tracking (Weekly)
                        try:
                            usage = response.usage_metadata
                            self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
                        except: pass

                        summary = response.text.strip()
                    except Exception as e:
                        logger.error(f"Weekly AI failed: {e}")
                        summary = "주간 일정 요약 실패"

                    msg = f"📅 <b>주간 학사 일정 ({start_date} ~ {end_date})</b>\n\n{summary}\n\n<a href='{target.url}'>[전체 보기]</a>"
                    await self.send_telegram(session, msg, self.config.topic_map.get('학사'))
                    self.state.last_weekly_briefing = today_str
                    self._save_state()

        # 2. Daily Check
        check_type = None
        target_date = None
        label = ""
        if now.hour >= 6 and self.state.last_calendar_check_morning != today_str:
            check_type = 'morning'
            target_date = now.date()
            label = f"(오늘 {target_date.strftime('%m/%d')})"
        elif now.hour >= 18 and self.state.last_calendar_check_evening != today_str:
            check_type = 'evening'
            target_date = now.date() + datetime.timedelta(days=1)
            label = f"(내일 {target_date.strftime('%m/%d')})"

        if check_type:
            html = await self.fetch_page(session, target.url)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                text = soup.get_text(strip=True)
                
                prompt = self.config.calendar_prompt_template.format(target_date=target_date, text=text[:4000])
                
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(None, lambda: model.generate_content(prompt, generation_config={"response_mime_type": "application/json"}))
                    
                    # Token Tracking (Calendar)
                    try:
                        usage = response.usage_metadata
                        self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
                    except: pass

                    result = json.loads(response.text)
                except Exception as e:
                    logger.error(f"Calendar AI failed: {e}")
                    result = {}
                
                summary = result.get('content', '일정이 없습니다.')
                event_date = result.get('event_date', str(target_date))
                
                msg = f"📅 <b>학사 일정 {label}</b>\n\n{summary}\n\n<a href='{target.url}'>[전체 보기]</a>"

                # Calendar Button for Daily Schedule
                buttons = []
                if event_date and summary != '일정이 없습니다.':
                     title_text = summary if len(summary) < 30 else summary[:30] + "..."
                     cal_url = self.generate_calendar_url(f"학사 일정: {title_text}", event_date)
                     if cal_url: buttons.append({"text": "📅 캘린더 등록", "url": cal_url})

                await self.send_telegram(session, msg, self.config.topic_map.get('학사'), buttons=buttons)
                
                if check_type == 'morning': self.state.last_calendar_check_morning = today_str
                else: self.state.last_calendar_check_evening = today_str
                self._save_state()

    async def process_menu(self, session: aiohttp.ClientSession, target: TargetConfig):
        logger.info(f"Checking Menu {target.name}...")
        try:
            html = await self.fetch_page(session, target.url)
            if not html: return
            
            # Parse list (reuse parse_list or custom if needed, assuming standard board format)
            items = self.parse_list(html, None, target.url)
            if not items: return

            for item in items:
                # Check DB
                if self.supabase:
                    resp = self.supabase.table('notices').select('*').eq('site_key', target.key).eq('article_id', item.id).execute()
                    if resp.data: continue # Already processed

                full_url = urllib.parse.urljoin(target.url, item.link)
                detail_html = await self.fetch_page(session, full_url)
                if not detail_html: continue

                soup = BeautifulSoup(detail_html, 'html.parser')
                content_div = soup.select_one(target.content_selector)
                
                # Find Image
                img_url = None
                if content_div:
                    for img in content_div.find_all('img'):
                        src = img.get('src', '')
                        if src and 'file' not in src.lower():
                            img_url = urllib.parse.urljoin(target.base_url, src)
                            break
                
                if not img_url: continue

                # Download Image
                img_data = None
                try:
                    async with session.get(img_url, headers={'Referer': full_url}) as resp:
                        if resp.status == 200:
                            img_data = await resp.read()
                except Exception as e:
                    logger.error(f"Failed to download menu image: {e}")
                    continue

                if not img_data: continue

                # Gemini Vision Analysis
                summary = "식단 분석 실패"
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    prompt = self.config.menu_prompt_template or "Extract menu for Today and Tomorrow from this image."
                    
                    image_part = {"mime_type": "image/jpeg", "data": img_data}
                    
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(None, lambda: model.generate_content([prompt, image_part]))
                    
                    # Token Tracking
                    try:
                        usage = response.usage_metadata
                        self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
                    except: pass

                    summary = response.text.strip()
                except Exception as e:
                    logger.error(f"Menu OCR failed: {e}")

                # Save to DB
                if self.supabase:
                    db_data = {
                        'site_key': target.key,
                        'article_id': item.id,
                        'title': item.title,
                        'url': full_url,
                        'category': '식단',
                        'content_hash': self.calculate_hash(summary), # Use summary as hash
                        'summary': summary,
                        'is_useful': True,
                        'updated_at': datetime.datetime.now(KST).isoformat()
                    }
                    self.supabase.table('notices').upsert(db_data, on_conflict='site_key,article_id').execute()

                # Send Telegram
                msg = (
                    f"🍱 <b>{target.name}</b>\n"
                    f"<a href='{full_url}'>{self.escape_html(item.title)}</a>\n\n"
                    f"{summary}"
                )
                await self.send_telegram(session, msg, self.config.topic_map.get('dormitory'), photo_data=img_data)

        except Exception as e:
            logger.error(f"Process Menu failed: {e}")

    async def cleanup_old_data(self):
        if not self.supabase: return
        try:
            # Retention: Delete data older than 2 years
            two_years_ago = (datetime.datetime.now(KST) - datetime.timedelta(days=365*2)).isoformat()
            self.supabase.table('notices').delete().lt('created_at', two_years_ago).execute()
            logger.info("Cleaned up old data.")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    async def run(self):
        logger.info("🚀 ASYNC SCRAPER STARTED (CDC + ARCHIVING)")
        
        # Retention Policy Check
        await self.cleanup_old_data()

        # Unpin Expired
        now_date = datetime.datetime.now(KST).date()
        active_pins = []
        async with aiohttp.ClientSession() as session:
            # Health Check (Morning)
            if datetime.datetime.now(KST).hour == 8:
                await self.send_health_check(session)

            for pin in self.state.pinned_exams:
                try:
                    end_date = datetime.datetime.strptime(pin['end_date'], '%Y-%m-%d').date()
                    if now_date > end_date:
                        await self.unpin_message(session, pin['message_id'])
                    else:
                        active_pins.append(pin)
                except:
                    active_pins.append(pin)
            
            if len(active_pins) != len(self.state.pinned_exams):
                self.state.pinned_exams = active_pins
                self._save_state()

            # Process Targets
            for target in self.config.targets:
                if target.type == 'calendar':
                    await self.process_calendar(session, target)
                elif target.type == 'menu':
                    await self.process_menu(session, target)
                else:
                    await self.process_target(session, target)

            # Daily Summary
            if datetime.datetime.now(KST).hour >= 18:
                today = datetime.datetime.now(KST).strftime('%Y-%m-%d')
                if self.state.last_daily_summary != today:
                    if not self.state.daily_notices_buffer:
                        self.state.daily_notices_buffer = {}
                        
                    buffer = self.state.daily_notices_buffer
                    if buffer.get('date') == today and buffer.get('items'):
                        # Count categories
                        counts = {}
                        for item in buffer['items']:
                            match = re.match(r'\[(.*?)\]', item)
                            if match:
                                cat = match.group(1)
                                counts[cat] = counts.get(cat, 0) + 1
                        
                        summary_lines = [f"- {k}: {v}건" for k, v in counts.items()]
                        msg = "📢 <b>오늘의 공지 요약</b>\n\n" + "\n".join(summary_lines)
                        await self.send_telegram(session, msg, self.config.topic_map.get('일반'))
                        self.state.last_daily_summary = today
                        self._save_state()

            # Check for User Commands (/search) - REMOVED
            # await self.check_commands(session)

if __name__ == "__main__":
    scraper = NoticeScraper()
    asyncio.run(scraper.run())
