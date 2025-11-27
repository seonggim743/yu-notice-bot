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
import html
from bs4 import BeautifulSoup
import google.generativeai as genai
from supabase import create_client, Client
from typing import List, Dict, Optional, Any
import urllib.parse
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from models import BotConfig, NoticeItem, ScraperState, TargetConfig, Attachment
from telegram_client import send_telegram

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

# Logging Configuration
handlers = [logging.StreamHandler()]

# Add File Handler if NOT in GitHub Actions (Local/Server environment)
if not os.environ.get('GITHUB_ACTIONS'):
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    handlers.append(file_handler)

for h in handlers:
    h.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))

logger.handlers = handlers
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
            async with session.get(url, timeout=30) as response:
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
            logger.info(f"💰 Token Usage: {prompt_tokens} + {completion_tokens} = {prompt_tokens + completion_tokens}")
        except Exception as e:
            logger.error(f"Token usage insert failed: {e}")

    async def get_ai_diff_summary(self, old_text: str, new_text: str) -> str:
        """Generates a summary of changes between old and new text."""
        if not os.environ.get('GEMINI_API_KEY'): return "내용 변경 (AI Key Missing)"
        
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = (
                "Compare the following two versions of a notice and summarize the changes in Korean.\n"
                "Output ONLY the summary of what changed (e.g., '신청 기간이 11/25에서 11/30으로 연장되었습니다.').\n"
                "Keep it concise (1 sentence).\n\n"
                f"--- OLD VERSION ---\n{old_text[:2000]}\n\n"
                f"--- NEW VERSION ---\n{new_text[:2000]}"
            )
            
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
            
            # Token Tracking
            try:
                usage = response.usage_metadata
                self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
            except: pass

            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Diff failed: {e}")
            return "내용 변경 (AI 분석 실패)"

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
                today = now.date()
                start_of_day = datetime.datetime.combine(today, datetime.time.min).isoformat()
                
                # RPD (Requests Per Day) - Count rows for today
                resp_today = self.supabase.table('token_usage').select('id', count='exact').gte('timestamp', start_of_day).execute()
                rpd_count = resp_today.count if resp_today.count is not None else len(resp_today.data)
                
                # Total Tokens Today
                resp_tokens = self.supabase.table('token_usage').select('total_tokens').gte('timestamp', start_of_day).execute()
                tokens_today = sum(row['total_tokens'] for row in resp_tokens.data)
                
                # Limits (Gemini 2.5 Flash)
                LIMIT_RPD = 250
                LIMIT_TPM = "250k"
                LIMIT_RPM = 10
                
                token_msg = (
                    f"📊 <b>Token Usage (Today)</b>\n"
                    f"<pre>"
                    f"RPD:   {rpd_count}/{LIMIT_RPD} (Reqs)\n"
                    f"Token: {tokens_today:,}\n"
                    f"Limits: {LIMIT_RPM} RPM, {LIMIT_TPM} TPM"
                    f"</pre>"
                )
            except Exception as e:
                logger.error(f"Failed to fetch token stats: {e}")

        msg = (
            f"✅ <b>System Health Check</b>\n"
            f"🟢 Status: Online\n"
            f"🗄 Supabase: {'Connected' if self.supabase else 'Disconnected'}\n\n"
            f"{token_msg}"
        )
        await send_telegram(session, msg, self.config.topic_map.get('일반'))

    async def send_error_report(self, error: Exception):
        """Sends a critical error report to the developer/admin."""
        import traceback
        
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        # Truncate if too long for Telegram
        if len(tb_str) > 3000: tb_str = tb_str[-3000:]
        
        # Escape HTML to prevent 400 Bad Request
        safe_tb = html.escape(tb_str)
        safe_error = html.escape(str(error))
        
        msg = (
            f"🚨 <b>CRITICAL ERROR</b>\n\n"
            f"<b>Type:</b> {type(error).__name__}\n"
            f"<b>Message:</b> {safe_error}\n\n"
            f"<pre>{safe_tb}</pre>"
        )
        
        # Send to General Topic (ID 1) as requested
        try:
            async with aiohttp.ClientSession() as session:
                await send_telegram(session, msg, 1)
        except Exception as e:
            logger.error(f"Failed to send error report: {e}")

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

    def parse_list(self, html: str, last_id: Optional[str], target: TargetConfig) -> List[NoticeItem]:
        soup = BeautifulSoup(html, 'html.parser')
        items = []
        rows = soup.select(target.list_selector)

        for row in rows:
            try:
                # Dynamic Selector
                title_link = row.select_one(target.title_selector)
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

                # Dynamic Link Selector (usually same as title, but can be different)
                link_element = row.select_one(target.link_selector)
                link = link_element.get('href') if link_element else title_link.get('href')
                
                parsed_url = urllib.parse.urlparse(link)
                qs = urllib.parse.parse_qs(parsed_url.query)
                article_id = qs.get('articleNo', [None])[0]
                
                if not article_id: continue

                # Sticky Logic (Best Effort default, can be improved later)
                is_sticky = 'b-top-box' in row.get('class', []) or '공지' in row.get_text()
                
                # Attachments
                attachments = []
                seen_urls = set()
                for fl in row.select('a[href*="fileDownload"]'):
                    f_url = urllib.parse.urljoin(target.url, fl.get('href'))
                    if f_url not in seen_urls:
                        seen_urls.add(f_url)
                        attachments.append(Attachment(text=f"📄 {fl.get_text(strip=True) or '첨부'}", url=f_url))

                # Image
                image_url = None
                for img in row.find_all('img'):
                    src = img.get('src', '')
                    if 'file' not in src.lower() and 'icon' not in src.lower():
                        image_url = urllib.parse.urljoin(target.url, src)
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
        items = self.parse_list(html, None, target)
        if not items: return

        # Reverse order to process Oldest -> Newest
        items.reverse()

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
            content_html = str(content_div) if content_div else ""
            
            # Calculate Hash (Body Only)
            current_body_hash = self.calculate_hash(content_text)
            
            is_new = db_record is None
            is_modified = False
            reuse_summary = False
            modified_reasons = []

            if db_record:
                old_hash = db_record.get('content_hash')
                old_title = db_record.get('title')
                
                # 1. Check New Logic (Body Only) - For migrated records
                if old_hash == current_body_hash:
                    if old_title != item.title:
                        is_modified = True
                        modified_reasons.append("제목 변경")
                        reuse_summary = True
                    else:
                        continue # Nothing changed
                
                # 2. Check Old Logic (Total Hash) - For legacy records
                elif old_hash == self.calculate_hash(item.title + content_text):
                    # Unchanged, but needs migration to Body Hash
                    # Update DB silently to avoid future confusion
                    if self.supabase:
                        try:
                            self.supabase.table('notices').update({'content_hash': current_body_hash}).eq('site_key', target.key).eq('article_id', item.id).execute()
                        except Exception as e:
                            logger.error(f"Hash migration failed: {e}")
                    continue
                    
                # 3. Check Mixed Case (Title changed, but Content might be same as Old Total Hash)
                elif old_title != item.title and old_hash == self.calculate_hash(old_title + content_text):
                     # Content is same, only Title changed
                     is_modified = True
                     modified_reasons.append("제목 변경")
                     reuse_summary = True
                     
                else:
                    # Real Content Change
                    is_modified = True
                    modified_reasons.append("내용 변경")

            if not is_new and not is_modified:
                continue # No change, skip

            logger.info(f"Found {'New' if is_new else 'Modified'} item: {item.title}")
            
            # Reply Feature: Get Original Message ID
            reply_to_msg_id = None
            if is_modified and db_record:
                reply_to_msg_id = db_record.get('message_id')

            # Diff Summary Logic
            diff_summary = ""
            if is_modified and "내용 변경" in modified_reasons:
                old_content = db_record.get('content', '') if db_record else ""
                if old_content:
                    diff_summary = await self.get_ai_diff_summary(old_content, content_text)
                    if diff_summary:
                        modified_reasons = [r for r in modified_reasons if r != "내용 변경"] # Remove generic reason
                        modified_reasons.append(diff_summary)
                else:
                    # Legacy record without content
                    pass

            # Logic for Image, AI, etc. (Same as before)
            final_image_url = item.image_url
            if not final_image_url and content_div:
                img = content_div.find('img')
                if img:
                    src = img.get('src', '')
                    if src and 'file' not in src.lower():
                        final_image_url = urllib.parse.urljoin(target.base_url, src)
            
            # Debug Image Detection
            if final_image_url:
                logger.info(f"Image detected for {item.title}: {final_image_url}")
            else:
                logger.info(f"No image detected for {item.title}")

            photo_data = None
            if final_image_url:
                try:
                    async with session.get(final_image_url, headers={'Referer': full_url}) as resp:
                        if resp.status == 200: photo_data = await resp.read()
                except: pass

            # Optimization: Skip AI for short text + image (likely just an image post)
            # Threshold: < 50 chars and has image
            should_skip_ai = len(content_text) < 50 and (final_image_url or item.attachments)
            
            if reuse_summary and db_record:
                analysis = {
                    "useful": db_record.get('is_useful', True),
                    "category": db_record.get('category', '일반'),
                    "summary": db_record.get('summary', ''),
                    "start_date": db_record.get('start_date'),
                    "end_date": db_record.get('end_date')
                }
                logger.info(f"Reusing summary for {item.title}")
            elif should_skip_ai:
                analysis = {
                    "useful": True, 
                    "category": "일반", 
                    "summary": content_text if content_text else "이미지/첨부파일 공지입니다."
                }
            else:
                analysis = await self.get_ai_analysis(content_text, self.config.ai_prompt_template)
            
            # Post-Process Analysis Summary
            summary_raw = analysis.get('summary', '')
            summary_lines = []

            if isinstance(summary_raw, list):
                for s in summary_raw:
                    s_str = str(s).strip()
                    if s_str and s_str not in ['-', '']:
                        summary_lines.append(s_str)
            else:
                # Split by newline to handle existing multi-line strings
                raw_lines = str(summary_raw).split('\n')
                for s in raw_lines:
                    s_str = s.strip()
                    if s_str and s_str not in ['-', '']:
                        summary_lines.append(s_str)

            # Format with bullets
            final_lines = []
            for line in summary_lines:
                # Remove existing bullets to normalize (-, *, •)
                clean_line = re.sub(r'^[-*•]\s*', '', line).strip()
                if clean_line:
                    final_lines.append(f"- {clean_line}")
            
            summary_str = '\n'.join(final_lines)

            analysis['summary'] = summary_str
            
            # Archiving (Save to DB)
            if self.supabase:
                try:
                    db_data = {
                        'site_key': target.key,
                        'article_id': item.id,
                        'title': item.title,
                        'url': full_url,
                        'category': analysis.get('category', '일반'),
                        'content_hash': current_body_hash,
                        'content': content_text, # Store Plain Text
                        'html_content': content_html, # Store HTML
                        'summary': str(analysis.get('summary', '')),
                        'is_useful': analysis.get('useful', True),
                        'start_date': analysis.get('start_date'),
                        'end_date': analysis.get('end_date'),
                        'attachments': [a.dict() for a in item.attachments],
                        'image_url': final_image_url,
                        'updated_at': datetime.datetime.now(KST).isoformat()
                    }
                    # Upsert first to get ID, but we need to update message_id AFTER sending.
                    # Actually, we can just upsert here, and then update message_id later.
                    self.supabase.table('notices').upsert(db_data, on_conflict='site_key,article_id').execute()
                except Exception as e:
                    logger.error(f"Archiving failed: {e}")

            if not analysis.get('useful', True):
                logger.info(f"Skipping {item.title} (AI marked as not useful)")
                continue

            # Send Notification
            category = analysis.get('category', '일반')
            summary = analysis.get('summary', '')
            if isinstance(summary, list): summary = '\n'.join(summary)
            
            is_exam = any(k in item.title for k in ["시험", "중간고사", "기말고사", "강의평가"])
            if is_exam:
                category = "시험/학사"
            if is_exam:
                category = "시험/학사"
                # Keep AI dates if available, otherwise might need manual extraction logic (omitted for now)

            topic_id = self.config.topic_map.get(category, 0)
            if is_exam and topic_id == 0: topic_id = self.config.topic_map.get('학사', 4)
            
            # Force Dormitory Topic
            if target.key == 'dorm_notice':
                topic_id = self.config.topic_map.get('dormitory', 15)

            safe_title = self.escape_html(item.title)
            safe_summary = self.escape_html(str(summary))
            
            prefix = "🆕 " if is_new else "🔄 "
            
            modified_text = ""
            if is_modified:
                reason = f" ({', '.join(modified_reasons)})" if modified_reasons else ""
                modified_text = f"\n\n(수정됨{reason})"

            # Keyword Alert
            keyword_alert = ""
            if self.config.keywords:
                matched_keywords = [k for k in self.config.keywords if k in item.title]
                if matched_keywords:
                    keyword_alert = f"\n🚨 <b>키워드 알림: {', '.join(matched_keywords)}</b>"

            msg = (
                f"{prefix}<b>{self.escape_html(target.name)}</b>\n"
                f"<a href='{full_url}'><b>{safe_title}</b></a>\n"
                f"\n📝 <b>요약 ({category})</b>\n{safe_summary}\n{modified_text}\n{keyword_alert}\n"
                f"#알림 #{category}"
            )

            buttons = []
            
            # UX: Add Calendar Button
            start_date = analysis.get('start_date')
            end_date = analysis.get('end_date')
            
            if start_date or end_date:
                # If only end_date, treat as all-day event on that day
                # If both, treat as range
                cal_date = start_date if start_date else end_date
                cal_title = f"[{category}] {item.title}"
                
                # If we have a range, we might want to pass both to generate_calendar_url
                # But current generate_calendar_url only takes one date (start).
                # Let's update generate_calendar_url signature or logic later. 
                # For now, use start_date if available, else end_date.
                
                cal_url = self.generate_calendar_url(cal_title, cal_date)
                if cal_url:
                    label = "📅 일정 등록"
                    if end_date: label += f" (~{end_date[5:]})"
                    buttons.append({"text": label, "url": cal_url})

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

            msg_id = await send_telegram(session, msg, topic_id, buttons, photo_data, reply_to_message_id=reply_to_msg_id)
            
            # Save Message ID
            if msg_id and self.supabase:
                try:
                    self.supabase.table('notices').update({'message_id': msg_id}).eq('site_key', target.key).eq('article_id', item.id).execute()
                except Exception as e:
                    logger.error(f"Failed to save message_id: {e}")

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
                        f"Output Format: 'MM/DD (Day): Event' (Korean)\n"
                        f"CRITICAL: If there are NO events on {start_date}, find the NEXT event within 7 days.\n"
                        f"If found, output: '🔜 다가오는 일정 (MM/DD): Event'\n"
                        f"If nothing in 7 days, return '일정이 없습니다.'"
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
                    await send_telegram(session, msg, self.config.topic_map.get('학사'))
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

                await send_telegram(session, msg, self.config.topic_map.get('학사'), buttons=buttons)
                
                # Save to DB
                if self.supabase and summary != '일정이 없습니다.':
                    try:
                        db_data = {
                            'event_date': str(event_date),
                            'content': summary,
                            'created_at': datetime.datetime.now(KST).isoformat()
                        }
                        self.supabase.table('calendar_events').upsert(db_data, on_conflict='event_date,content').execute()
                    except Exception as e:
                        logger.error(f"Failed to save calendar event: {e}")
                
                if check_type == 'morning': self.state.last_calendar_check_morning = today_str
                else: self.state.last_calendar_check_evening = today_str
                self._save_state()

    async def process_menu(self, session: aiohttp.ClientSession, target: TargetConfig):
        logger.info(f"Checking Menu {target.name}...")
        try:
            html = await self.fetch_page(session, target.url)
            if not html: return
            
            # Parse list (reuse parse_list or custom if needed, assuming standard board format)
            items = self.parse_list(html, None, target)
            if not items: return

            # Only process the LATEST menu post to avoid spamming old history
            # Expanded to top 5 to handle pinned posts or missed updates
            items = items[:5]

            # Filter and Sort Items by Date
            candidates = []
            for item in items:
                # Default: Assume it's relevant if we can't parse date
                item_date = datetime.date.min
                
                # Date Parsing (Heuristic)
                try:
                    # Regex to capture the END date part: "~ 11/24" or "~ 11월 24일"
                    date_match = re.search(r'~\s*(\d{1,2})[./월]\s*(\d{1,2})', item.title)
                    if date_match:
                        end_month = int(date_match.group(1))
                        end_day = int(date_match.group(2))
                        current_year = datetime.datetime.now(KST).year
                        
                        year = current_year
                        now_date = datetime.datetime.now(KST).date()
                        # Year rollover check
                        if now_date.month == 1 and end_month == 12:
                            year -= 1
                        elif now_date.month == 12 and end_month == 1:
                            year += 1
                            
                        try:
                            item_date = datetime.date(year, end_month, end_day)
                        except ValueError: pass
                except: pass
                
                candidates.append({'item': item, 'date': item_date})

            # Sort by Date Descending (Latest first)
            # If dates are equal (or min), preserve original order (assuming top is newer)
            candidates.sort(key=lambda x: x['date'], reverse=True)

            if not candidates: return
            
            # Pick the BEST candidate
            # We only want to process the SINGLE latest menu to avoid confusion/overwriting pins.
            best_candidate = candidates[0]
            target_item = best_candidate['item']
            target_date = best_candidate['date']
            
            # Sanity Check: If the best candidate is significantly old (e.g. > 10 days), maybe warn or skip?
            # But user wants "Latest", so even if it's old, it's the "Latest available".
            # However, if it's REALLY old, we probably shouldn't pin it as "New".
            # Let's keep the "7 days" check but apply it only to the chosen one.
            
            if target_date != datetime.date.min:
                 if (datetime.datetime.now(KST).date() - target_date).days > 7:
                     logger.info(f"Latest menu is too old ({target_date}). Skipping.")
                     return

            # Process ONLY the target item
            # Wrap in a list to keep existing structure minimal change if needed, 
            # but we can just process directly.
            items_to_process = [target_item]

            for item in items_to_process:
                # Check DB (Optimization)
                db_record = None
                if self.supabase:
                    resp = self.supabase.table('notices').select('*').eq('site_key', target.key).eq('article_id', item.id).execute()
                    if resp.data:
                        db_record = resp.data[0]
                        # Optimization: If ID exists, assume it's processed and skip.
                        # User requested to save tokens and bandwidth.
                        logger.info(f"Skipping existing menu ID: {item.id} ({item.title})")
                        continue

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

                # Calculate Hash (Image Hash)
                # Use SHA256 of the image binary data for deterministic change detection.
                # Previous logic used OCR text which varies slightly, causing loops.
                current_hash = hashlib.sha256(img_data).hexdigest()
                
                is_new = db_record is None
                is_modified = db_record and db_record.get('content_hash') != current_hash
                
                if not is_new and not is_modified:
                    continue

                # Gemini Vision Analysis (Full Week)
                summary = "식단 분석 실패"
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    # Prompt: Extract ALL text for the whole week
                    # Prompt: Extract ALL menu text from this image for the entire week (Mon-Sun, Breakfast/Lunch/Dinner). Return raw text.
                    prompt = (
                        "Extract the weekly menu from this image.\n"
                        "Return a JSON LIST of objects, where each object represents a day.\n"
                        "Format: [{\"date\": \"YYYY-MM-DD\", \"breakfast\": \"...\", \"lunch\": \"...\", \"dinner\": \"...\"}, ...]\n"
                        "Infer the year and month from the image text if possible. If only day number is shown, assume current month/year.\n"
                        "Return ONLY the JSON string. Do not include 'Here is...' or markdown formatting."
                    )
                    
                    image_part = {"mime_type": "image/jpeg", "data": img_data}
                    
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(None, lambda: model.generate_content([prompt, image_part], generation_config={"response_mime_type": "application/json"}))
                    
                    # Token Tracking
                    try:
                        usage = response.usage_metadata
                        self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
                    except: pass

                    summary = response.text.strip()
                    # Clean up markdown if present (though response_mime_type should handle it, safety first)
                    if summary.startswith("```json"):
                        summary = summary.replace("```json", "").replace("```", "")
                    summary = summary.strip()
                except Exception as e:
                    logger.error(f"Menu OCR failed: {e}")

                # Save to DB (Full Text)
                if self.supabase:
                    db_data = {
                        'site_key': target.key,
                        'article_id': item.id,
                        'title': item.title,
                        'url': full_url,
                        'category': '식단',
                        'content_hash': current_hash, # Use Image Hash
                        'summary': summary,
                        'image_url': img_url,
                        'is_useful': True,
                        'attachments': [a.dict() for a in item.attachments],
                        'updated_at': datetime.datetime.now(KST).isoformat()
                    }
                    self.supabase.table('notices').upsert(db_data, on_conflict='site_key,article_id').execute()

                # Send Telegram (Image Only - "Updated")
                msg = (
                    f"🍱 <b>{target.name} 업데이트</b>\n"
                    f"<a href='{full_url}'>{self.escape_html(item.title)}</a>\n\n"
                    f"식단표가 등록되었습니다. 매일 아침 당일 식단이 발송됩니다."
                )
                
                # Unpin old menu if exists
                if self.state.last_menu_message_id:
                    await self.unpin_message(session, self.state.last_menu_message_id)

                msg_id = await send_telegram(session, msg, self.config.topic_map.get('dormitory'), photo_data=img_data)
                
                if msg_id:
                    await self.pin_message(session, msg_id)
                    self.state.last_menu_message_id = msg_id
                    self._save_state()
                
                # Trigger Daily Check Immediately
                await self.check_daily_menu(session, force=True)

        except Exception as e:
            logger.error(f"Process Menu failed: {e}")

    async def check_daily_menu(self, session: aiohttp.ClientSession, force: bool = False):
        if not self.supabase: return
        
        now = datetime.datetime.now(KST)
        today_str = now.strftime('%Y-%m-%d')
        
        # Run only in morning (e.g. 7am) or if forced
        if not force:
            if now.hour < 7: return
            if self.state.last_daily_menu_check == today_str: return

        try:
            # Fetch latest menu from DB (Check top 5 to handle "Next Week" posted early or other notices)
            resp = self.supabase.table('notices').select('*').eq('category', '식단').order('article_id', desc=True).limit(5).execute()
            if not resp.data: return
            
            for menu_data in resp.data:
                full_text = menu_data.get('summary', '')
                
                # AI: Extract Today's Menu
                prompt = (
                    f"Here is the weekly menu text:\n{full_text[:10000]}\n\n"
                    f"Task: Extract the menu for TODAY ({today_str}).\n"
                    f"Format: \n"
                    f"🍱 {today_str} 기숙사 식단\n"
                    f"☀️ 아침: ...\n"
                    f"🌤 점심: ...\n"
                    f"🌙 저녁: ...\n\n"
                    f"If no menu found for today, return '오늘은 식단이 없습니다.'"
                )
                
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
                    
                    # Token Tracking
                    try:
                        usage = response.usage_metadata
                        self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
                    except: pass
                    
                    result_text = response.text.strip()
                    
                    if "오늘은 식단이 없습니다" not in result_text:
                         await send_telegram(session, result_text, self.config.topic_map.get('dormitory'))
                         # If found, break loop (don't check previous week)
                         self.state.last_daily_menu_check = today_str
                         self._save_state()
                         return
                except Exception as e:
                    logger.error(f"Daily Menu AI failed: {e}")
                    continue

            # If loop finishes without sending, it means no menu found in top 2.
            logger.info(f"No daily menu found for {today_str} in top 2 posts.")
            self.state.last_daily_menu_check = today_str
            self._save_state()
            
        except Exception as e:
            logger.error(f"Daily Menu Check failed: {e}")

    async def send_weekly_deadline_briefing(self, session: aiohttp.ClientSession):
        if not self.supabase: return
        
        now = datetime.datetime.now(KST)
        today_str = now.strftime('%Y-%m-%d')
        
        # Run on Monday (0) at 9 AM
        if now.weekday() == 0 and now.hour >= 9:
            if self.state.last_deadline_briefing != today_str:
                try:
                    start_date = now.date()
                    end_date = start_date + datetime.timedelta(days=7)
                    
                    # Query DB for deadlines in the next 7 days
                    resp = self.supabase.table('notices') \
                        .select('title, end_date, url, category') \
                        .gte('end_date', start_date.isoformat()) \
                        .lte('end_date', end_date.isoformat()) \
                        .order('end_date') \
                        .execute()
                    
                    if resp.data:
                        lines = []
                        for item in resp.data:
                            d_str = item['end_date']
                            # Parse date to get weekday
                            d_obj = datetime.datetime.strptime(d_str, '%Y-%m-%d')
                            weekday_kor = ["월", "화", "수", "목", "금", "토", "일"][d_obj.weekday()]
                            
                            lines.append(f"- <a href='{item['url']}'>{self.escape_html(item['title'])}</a> (~{d_str[5:]} {weekday_kor})")
                        
                        if lines:
                            msg = (
                                f"⏰ <b>이번 주 마감 일정 ({start_date.strftime('%m/%d')} ~ {end_date.strftime('%m/%d')})</b>\n\n"
                                + "\n".join(lines)
                                + "\n\n#마감알림"
                            )
                            # Send to General Topic
                            await send_telegram(session, msg, self.config.topic_map.get('일반'))
                            
                    self.state.last_deadline_briefing = today_str
                    self._save_state()
                    
                except Exception as e:
                    logger.error(f"Weekly Deadline Briefing failed: {e}")

    async def cleanup_old_data(self):
        if not self.supabase: return
        try:
            # Retention: Delete data older than 2 years
            two_years_ago = (datetime.datetime.now(KST) - datetime.timedelta(days=365*2)).isoformat()
            self.supabase.table('notices').delete().lt('created_at', two_years_ago).execute()
            
            # Content Cleanup: Clear 'content' older than 3 months to save space
            three_months_ago = (datetime.datetime.now(KST) - datetime.timedelta(days=90)).isoformat()
            self.supabase.table('notices').update({'content': None}).lt('created_at', three_months_ago).execute()
            
            # Invalid Data Cleanup: Delete notices with empty content (if not an image post)
            # We assume if content is empty AND image_url is null AND attachments is empty, it's invalid.
            # Or just delete if 'content' is empty string and 'summary' is empty?
            # User request: "delete the one that does not have the text content"
            # Let's be safe: Delete if content is empty AND summary is empty/failed.
            self.supabase.table('notices').delete().eq('content', '').eq('summary', '').execute()
            
            logger.info("Cleaned up old and invalid data.")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    async def run(self):
        try:
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
                            # Group by category
                            grouped_items = {}
                            for item in buffer['items']:
                                # item format: "[category] <a href='...'>title</a>"
                                match = re.match(r'\[(.*?)\] (.*)', item)
                                if match:
                                    cat = match.group(1).replace('"', '').replace("'", "")
                                    content = match.group(2)
                                    if cat not in grouped_items: grouped_items[cat] = []
                                    grouped_items[cat].append(content)
                            
                            summary_blocks = []
                            for cat, items in grouped_items.items():
                                block = f"<b>[{cat}]</b>\n" + "\n".join([f"- {i}" for i in items])
                                summary_blocks.append(block)

                            msg = "📢 <b>오늘의 공지 요약</b>\n\n" + "\n\n".join(summary_blocks)
                            await send_telegram(session, msg, self.config.topic_map.get('일반'))
                            self.state.last_daily_summary = today
                            self._save_state()

                # Daily Menu Check
                await self.check_daily_menu(session)

                # Weekly Deadline Briefing
                await self.send_weekly_deadline_briefing(session)

        except Exception as e:
            logger.critical(f"🔥 FATAL ERROR: {e}")
            await self.send_error_report(e)
            raise

            # Check for User Commands (/search) - REMOVED
            # await self.check_commands(session)

if __name__ == "__main__":
    scraper = NoticeScraper()
    asyncio.run(scraper.run())
