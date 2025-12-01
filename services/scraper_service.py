import asyncio
import aiohttp
import hashlib
from typing import List, Dict, Optional
from core.config import settings
from core.logger import get_logger
from models.notice import Notice
from repositories.notice_repo import NoticeRepository
from services.ai_service import AIService
from services.notification_service import NotificationService
from services.file_service import FileService
from parsers.html_parser import HTMLParser
from core.performance import get_performance_monitor

logger = get_logger(__name__)

class ScraperService:
    def __init__(self, init_mode: bool = False, no_ai_mode: bool = False):
        self.repo = NoticeRepository()
        self.ai = AIService()
        self.notifier = NotificationService()
        self.file_service = FileService()
        self.init_mode = init_mode
        self.no_ai_mode = no_ai_mode
        
        # Safety Limits
        self.ai_summary_count = 0
        self.MAX_AI_SUMMARIES = 10
        
        # Rate Limiting (Gemini 2.5 Flash: 10 RPM = 6 seconds per request)
        # Using 7s for safety margin + each notice has multiple AI calls
        self.AI_CALL_DELAY = 7.0  # 7 seconds between AI calls
        self.NOTICE_PROCESS_DELAY = 0.5  # 0.5 seconds between each notice
        
        # Define Targets (Hardcoded for now, could be in config/DB)
        self.targets = [
            {
                "key": "yu_news",
                "url": "https://hcms.yu.ac.kr/main/intro/yu-news.do",
                "base_url": "https://hcms.yu.ac.kr/main/intro/yu-news.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "cse_notice",
                "url": "https://www.yu.ac.kr/cse/community/notice.do",
                "base_url": "https://www.yu.ac.kr/cse/community/notice.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "bachelor_guide",
                "url": "https://hcms.yu.ac.kr/main/bachelor/bachelor-guide.do?mode=list&articleLimit=30",
                "base_url": "https://hcms.yu.ac.kr/main/bachelor/bachelor-guide.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "calendar",
                "url": "https://hcms.yu.ac.kr/main/bachelor/calendar.do",
                "base_url": "https://hcms.yu.ac.kr/main/bachelor/calendar.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "dormitory_notice",
                "url": "https://www.yu.ac.kr/dormi/community/notice.do",
                "base_url": "https://www.yu.ac.kr/dormi/community/notice.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "dormitory_menu",
                "url": "https://www.yu.ac.kr/dormi/community/menu.do",
                "base_url": "https://www.yu.ac.kr/dormi/community/menu.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            }
        ]

    def calculate_hash(self, notice: Notice) -> str:
        """Hash of Title + Content + Image + Attachments (name + URL)"""
        # Include attachment name AND url to detect file replacements
        sorted_atts = sorted([f"{a.name}|{a.url}" for a in notice.attachments])
        att_str = "".join(sorted_atts)
        
        # Include image URL (empty string if None)
        img_str = notice.image_url or ""
        
        raw = f"{notice.title}{notice.content}{img_str}{att_str}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def process_menu_notice(self, session: aiohttp.ClientSession, notice: Notice):
        """
        Special handling for Dormitory Menu notices.
        """
        if not notice.image_url:
            logger.warning(f"[MENU] Notice {notice.title} has no image, skipping menu extraction.")
            return

        logger.info(f"[MENU] Extracting menu from image: {notice.title}")
        
        # 1. AI Extraction
        menu_data = await self.ai.extract_menu_from_image(notice.image_url)
        if not menu_data or 'raw_text' not in menu_data:
            logger.error("[MENU] Failed to extract menu text")
            return

        # 2. Save to DB (menus table)
        # TODO: Implement MenuRepository if needed, for now just logging
        logger.info(f"[MENU] Extracted: {menu_data['start_date']} ~ {menu_data['end_date']}")
        
        # 3. Send & Pin to Telegram
        # Send Image
        # Send Text
        # Pin Text
        # Unpin Old
        await self.notifier.send_menu_notification(session, notice, menu_data)

    async def process_target(self, session: aiohttp.ClientSession, target: Dict):
        key = target['key']
        monitor = get_performance_monitor()
        
        with monitor.measure("scrape_target", {"key": key}):
            logger.info(f"[SCRAPER] Scraping {key}...")
        
        try:
            async with session.get(target['url'], timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                html = await resp.text()
        except asyncio.TimeoutError:
            logger.error(f"[SCRAPER] Timeout fetching {key} after 30s")
            return
        except aiohttp.ClientError as e:
            logger.error(f"[SCRAPER] HTTP error fetching {key}: {e}")
            return
        except Exception as e:
            logger.error(f"[SCRAPER] Unexpected error fetching {key}: {e}")
            return

        parser = target['parser']
        items = parser.parse_list(html, key, target['base_url'])
        
        # IMPORTANT: Process oldest first (reverse chronological order)
        items.reverse()
        
        # Get already processed IDs
        processed_ids = self.repo.get_last_processed_ids(key, limit=1000)
        
        for item in items:
            is_new = item.article_id not in processed_ids
            old_hash = processed_ids.get(item.article_id)
            
            # Fetch detail
            try:
                async with session.get(item.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    detail_html = await resp.text()
            except asyncio.TimeoutError:
                logger.warning(f"[SCRAPER] Timeout fetching detail for {item.title}")
                continue
            except Exception as e:
                logger.warning(f"[SCRAPER] Failed to fetch detail for {item.title}: {e}")
                continue
            
            item = parser.parse_detail(detail_html, item)
            
            # Detect empty content (Allow if has attachments or image)
            has_media = bool(item.attachments or item.image_url)
            if (not item.content or len(item.content.strip()) < 10) and not has_media:
                logger.warning(f"[SCRAPER] Empty or very short content for '{item.title}' and no media. Skipping.")
                continue
            
            # Sanitize content (remove null bytes)
            if item.content:
                item.content = item.content.replace('\x00', '')
            
            # --- ATTACHMENT TEXT EXTRACTION & PREVIEW (Tier 1) ---
            if item.attachments:
                extracted_texts = []
                pdf_preview_count = 0
                MAX_PDF_PREVIEWS = 3
                
                # Limit to first 5 attachments for processing to avoid timeout
                for att in item.attachments[:5]:
                    ext = att.name.split('.')[-1].lower() if '.' in att.name else ''
                    
                    # 1. Text Extraction (HWP, PDF)
                    if ext in ['hwp', 'hwpx', 'pdf']:
                        logger.info(f"[SCRAPER] Downloading attachment for processing: {att.name}")
                        headers = {'Referer': item.url, 'User-Agent': settings.USER_AGENT}
                        file_data = await self.file_service.download_file(session, att.url, headers=headers)
                        
                        if file_data:
                            # Extract Text
                            text = self.file_service.extract_text(file_data, att.name)
                            if text:
                                text = text.strip()
                                if len(text) > 100:
                                    extracted_texts.append(f"--- 첨부파일: {att.name} ---\n{text[:3000]}...")
                                    logger.info(f"[SCRAPER] Extracted {len(text)} chars from {att.name}")
                            
                            # 2. PDF Preview Generation (Multi-PDF Support)
                            if ext == 'pdf' and pdf_preview_count < MAX_PDF_PREVIEWS:
                                logger.info(f"[SCRAPER] Generating PDF preview for {att.name}...")
                                preview_bytes = self.file_service.generate_preview_image(file_data, att.name)
                                if preview_bytes:
                                    att.preview_bytes = preview_bytes # Store in Attachment model
                                    pdf_preview_count += 1
                                    logger.info(f"[SCRAPER] PDF preview generated ({len(preview_bytes)} bytes)")

                if extracted_texts:
                    item.content += "\n\n" + "\n".join(extracted_texts)
            # -------------------------------------------

            logger.info(f"[SCRAPER] Content length for '{item.title}': {len(item.content)}")
            logger.info(f"[SCRAPER] Content preview: {item.content[:100]}")
            current_hash = self.calculate_hash(item)
            item.content_hash = current_hash
            
            is_modified = False
            modified_reason = ""
            
            if not is_new:
                if old_hash == current_hash:
                    continue  # No change
                else:
                    is_modified = True
                    modified_reason = "내용 또는 제목 변경됨"
            
            logger.info(f"Processing {'New' if is_new else 'Modified'}: {item.title}")
            
            # --- INIT MODE LOGIC ---
            if self.init_mode:
                logger.info(f"[INIT] Seeding database with: {item.title}")
                item.category = '일반'
                item.summary = "초기화 모드로 저장됨 (AI 요약 없음)"
                item.embedding = None
                
                # Save to DB only
                self.repo.upsert_notice(item)
                continue
            # -----------------------
            
            # --- NO-AI MODE LOGIC ---
            if self.no_ai_mode:
                logger.info(f"[NO-AI] Skipping AI analysis for: {item.title}")
                item.category = '일반'
                item.summary = "AI 분석 건너뜀 (No-AI Mode)"
                item.embedding = None
            else:
                # AI Analysis with rate limiting
                if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                    # Skip AI if content is too short (use content as summary)
                    # BUT if we extracted attachment text, content might be long now!
                    if len(item.content.strip()) < 50:
                        logger.info(f"[SCRAPER] Content too short for AI analysis. Using original content as summary.")
                        item.category = '일반'
                        item.summary = item.content.strip()
                        # Optional: Embed title + content
                        item.embedding = await self.ai.get_embedding(f"{item.title}\n{item.summary}")
                    else:
                        logger.info(f"[SCRAPER] Starting AI analysis ({self.ai_summary_count + 1}/{self.MAX_AI_SUMMARIES})...")
                        
                        # 1. Analyze content (Wait BEFORE first call)
                        logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before analyze_notice...")
                        await asyncio.sleep(self.AI_CALL_DELAY)
                        
                        with monitor.measure("ai_analysis", {"type": "summary", "title": item.title}):
                            analysis = await self.ai.analyze_notice(item.content, site_key=item.site_key)
                        
                        item.category = analysis.get('category', '일반')
                        item.tags = analysis.get('tags', [])  # NEW: Store AI-selected tags
                        item.summary = analysis.get('summary', item.content[:100])
                        
                        # Tier 2: Enhanced Metadata
                        item.deadline = analysis.get('deadline')
                        item.eligibility = analysis.get('eligibility', [])
                        item.start_date = analysis.get('start_date')
                        item.end_date = analysis.get('end_date')
                        item.target_grades = analysis.get('target_grades', [])
                        item.target_dept = analysis.get('target_dept')
                        
                        # 2. Get embedding (Wait BEFORE second call)
                        logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_embedding...")
                        await asyncio.sleep(self.AI_CALL_DELAY)
                        
                        item.embedding = await self.ai.get_embedding(f"{item.title}\n{item.summary}")
                        self.ai_summary_count += 1
                        
                        logger.info(f"[SCRAPER] AI complete. Quota: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}")
                else:
                    logger.warning(f"[SCRAPER] AI limit reached. Skipping AI analysis.")
                    item.category = '일반'
                    item.summary = item.content[:100] + " (AI 한도 도달)"
                    item.embedding = []
            
            # AI Diff for Modified
            if is_modified:
                old_notice = self.repo.get_notice(key, item.article_id)
                
                changes = {}
                if old_notice:
                    if old_notice.title != item.title:
                        changes['title'] = f"'{old_notice.title}' -> '{item.title}'"
                    
                    if old_notice.content != item.content:
                        # Store old and new content for detailed diff display
                        changes['old_content'] = old_notice.content
                        changes['new_content'] = item.content
                        
                        if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                            logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_diff_summary...")
                            await asyncio.sleep(self.AI_CALL_DELAY)
                            changes['content'] = await self.ai.get_diff_summary(old_notice.content, item.content)
                            self.ai_summary_count += 1  # Count diff as an AI call
                        else:
                            changes['content'] = "내용 변경됨 (AI 한도 초과)"
                    
                    # Image change detection
                    if old_notice.image_url != item.image_url:
                        old_img = old_notice.image_url or "없음"
                        new_img = item.image_url or "없음"
                        changes['image'] = f"{old_img} → {new_img}"
                    
                    # Attachments (name and URL)
                    old_atts = {a.name for a in old_notice.attachments}
                    new_atts = {a.name for a in item.attachments}
                    added = new_atts - old_atts
                    removed = old_atts - new_atts
                    
                    # Check for URL changes (same name, different URL)
                    old_att_map = {a.name: a.url for a in old_notice.attachments}
                    new_att_map = {a.name: a.url for a in item.attachments}
                    url_changed = []
                    for name in old_atts & new_atts:  # Common attachments
                        if old_att_map[name] != new_att_map[name]:
                            url_changed.append(name)
                    
                    if added or removed or url_changed:
                        att_changes = []
                        if added: att_changes.append(f"추가: {', '.join(added)}")
                        if removed: att_changes.append(f"제거: {', '.join(removed)}")
                        if url_changed: att_changes.append(f"재업로드: {', '.join(url_changed)}")
                        changes['attachments'] = ", ".join(att_changes)
                
                # [FIX] If hash changed but no actual changes detected (e.g. hash algo update),
                # update DB but do NOT notify.
                if not changes:
                    logger.info(f"[SCRAPER] Hash mismatch but no content changes detected for '{item.title}'. Updating hash only.")
                    self.repo.upsert_notice(item)
                    continue

                item.change_details = changes
                
                # Construct readable reason
                reasons = []
                if 'title' in changes: reasons.append("제목 변경")
                if 'content' in changes: reasons.append(f"내용 변경: {changes['content']}")
                if 'image' in changes: reasons.append("이미지 변경")
                if 'attachments' in changes: reasons.append(f"첨부파일 변경 ({changes['attachments']})")
                
                modified_reason = ", ".join(reasons) if reasons else "내용 변경됨"

            # Save to DB
            notice_id = self.repo.upsert_notice(item)
            
            if notice_id:
                # Notify
                msg_id = await self.notifier.send_telegram(session, item, is_new, modified_reason)
                if msg_id:
                    self.repo.update_message_ids(notice_id, 'telegram', msg_id)
                
                # Discord Notification
                # Check for existing thread ID if modified
                existing_thread_id = None
                if is_modified and 'old_notice' in locals() and old_notice:
                    existing_thread_id = old_notice.discord_thread_id
                
                discord_thread_id = await self.notifier.send_discord(session, item, is_new, modified_reason, existing_thread_id=existing_thread_id)
                
                if discord_thread_id:
                    self.repo.update_discord_thread_id(notice_id, discord_thread_id)
            
            # Small delay between notices
            await asyncio.sleep(self.NOTICE_PROCESS_DELAY)

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        
        # Complete browser headers required by YU site
        headers = {
            'User-Agent': settings.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers=headers
        ) as session:
            monitor = get_performance_monitor()
            
            with monitor.measure("full_scrape_run"):
                logger.info(f"[SCRAPER] Processing {len(self.targets)} targets sequentially...")
                
                for target in self.targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] Target {target['key']} failed: {e}")
            
            logger.info(f"[SCRAPER] Complete. Total AI calls: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}")
            monitor.log_summary()

    async def run_test(self, test_url: str):
        """
        Forces a notification for a specific URL.
        """
        from bs4 import BeautifulSoup
        
        logger.info(f"[TEST] Starting test run for: {test_url}")
        
        # 1. Identify Target
        target = None
        for t in self.targets:
            if t['base_url'] in test_url or t['url'] in test_url:
                target = t
                break
        
        if not target:
            # Fallback to generic parser if no target matches
            logger.warning(f"[TEST] No matching target found for {test_url}. Using generic parser.")
            target = self.targets[0] # Use first as default
        
        parser = target['parser']
        
        # 2. Fetch Content
        timeout = aiohttp.ClientTimeout(total=30)
        
        # Use same complete browser headers as main scraper
        headers = {
            'User-Agent': settings.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.yu.ac.kr/'
        }
        
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            try:
                async with session.get(test_url) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
                    
                # DEBUG: Save HTML to file
                with open("debug_html.html", "w", encoding="utf-8") as f:
                    f.write(html)
                logger.info("[TEST] Saved HTML to debug_html.html")
            except Exception as e:
                logger.error(f"[TEST] Failed to fetch URL: {e}")
                return

            # 3. Check if it's a list page (Auto-detect)
            # If the user provided a board URL (e.g. bachelor-guide.do), we should pick the first item
            soup = BeautifulSoup(html, 'html.parser')
            
            # Check if this URL matches any target's base_url
            matched_target = next((t for t in self.targets if t['url'] in test_url or test_url in t['url']), None)
            
            if matched_target:
                # Try to parse as list first
                logger.info(f"[TEST] URL matches target '{matched_target['key']}'. Checking if it's a list page...")
                items = matched_target['parser'].parse_list(html, matched_target['key'], matched_target['base_url'])
                
                if items:
                    logger.info(f"[TEST] Detected list page with {len(items)} items. Picking the first one for testing.")
                    first_item = items[0]
                    logger.info(f"[TEST] Redirecting test to: {first_item.title} ({first_item.url})")
                    
                    # Recursively call run_test with the item's URL
                    # But we need to be careful about infinite recursion if parsing fails
                    if first_item.url != test_url:
                        await self.run_test(first_item.url)
                        return
                    else:
                        logger.warning("[TEST] First item URL is same as list URL. Proceeding as detail page.")
            
            # 4. Parse (Simulate Item)
            # (Proceed with existing detail parsing logic)
            
            # Try to find title with YU-specific selectors first
            title = "Test Notification"
            title_selectors = [
                '.b-title-box',      # YU main title container
                '.b-view-title',     # YU view title
                '.view-title',       # Generic view title
                '.board-view-title', # Board view title
                'h1',                # Fallback to h1
                'h2',                # Fallback to h2
                'title'              # Last resort: page title
            ]
            
            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title_text = title_elem.get_text(strip=True)
                    # Skip if it's just whitespace or too short
                    if title_text and len(title_text) > 3:
                        # Clean up title
                        # 1. For <title> tag, remove site name suffix
                        if selector == 'title' and '|' in title_text:
                            title_text = title_text.split('|')[0].strip()
                        
                        # 2. Remove common markers (N=New, HOT, UP, etc.)
                        # These appear at the end of titles on YU notice boards
                        import re
                        # Remove single letter markers at the end (N, U, etc.)
                        title_text = re.sub(r'\s*[NUHOT]+\s*$', '', title_text)
                        # Remove "New" marker
                        title_text = re.sub(r'\s*New\s*$', '', title_text, flags=re.IGNORECASE)
                        
                        title = title_text.strip()
                        logger.info(f"[TEST] Found title with selector '{selector}': {title}")
                        break
            
            # Create dummy item
            item = Notice(
                site_key=target['key'],
                article_id="test_id",
                title=title,
                url=test_url,
                published_at=None
            )
            
            # Use parser to fill content and attachments
            item = parser.parse_detail(html, item)
            
            # --- ATTACHMENT TEXT EXTRACTION (Test Mode) ---
            if item.attachments:
                logger.info(f"[TEST] Found {len(item.attachments)} attachments. Attempting extraction...")
                extracted_texts = []
                for att in item.attachments[:2]:
                    ext = att.name.split('.')[-1].lower() if '.' in att.name else ''
                    if ext in ['hwp', 'hwpx', 'pdf']:
                        logger.info(f"[TEST] Downloading {att.name}...")
                        headers = {'Referer': item.url, 'User-Agent': settings.USER_AGENT}
                        file_data = await self.file_service.download_file(session, att.url, headers=headers)
                        
                        if file_data:
                            text = self.file_service.extract_text(file_data, att.name)
                            if text:
                                text = text.strip()
                                if len(text) > 50:
                                    extracted_texts.append(f"--- 첨부파일: {att.name} ---\n{text[:1000]}...")
                                    logger.info(f"[TEST] ✅ Extracted {len(text)} chars from {att.name}")
                                    logger.info(f"[TEST] Preview: {text[:200]}")
                                else:
                                    logger.warning(f"[TEST] Extracted text too short or empty.")
                            else:
                                logger.warning(f"[TEST] Extraction returned empty string.")
                        else:
                            logger.error(f"[TEST] Download failed.")
                
                if extracted_texts:
                    item.content += "\n\n" + "\n".join(extracted_texts)
                    logger.info(f"[TEST] Content updated with attachment text. New length: {len(item.content)}")
            # ----------------------------------------------
            
            # --- AI ANALYSIS (Test Mode) ---
            logger.info(f"[TEST] Starting AI analysis for verification...")
            analysis = await self.ai.analyze_notice(item.content, site_key=item.site_key)
            
            item.category = analysis.get('category', '일반')
            item.tags = analysis.get('tags', [])  # NEW: Store AI-selected tags
            item.summary = analysis.get('summary', item.content[:100])
            item.deadline = analysis.get('deadline')
            item.eligibility = analysis.get('eligibility', [])
            item.start_date = analysis.get('start_date')
            item.end_date = analysis.get('end_date')
            item.target_grades = analysis.get('target_grades', [])
            item.target_dept = analysis.get('target_dept')
            
            logger.info(f"[TEST] AI Result:")
            logger.info(f"  - Category: {item.category}")
            logger.info(f"  - Summary: {item.summary}")
            logger.info(f"  - Deadline: {item.deadline}")
            logger.info(f"  - Eligibility: {item.eligibility}")
            # -------------------------------

            logger.info(f"[TEST] Parsed Item: {item.title}")
            logger.info(f"[TEST] Content Length: {len(item.content)}")
            logger.info(f"[TEST] Attachments Found: {len(item.attachments)}")
            for att in item.attachments:
                logger.info(f"[TEST] - {att.name}: {att.url}")
            
            # 4. Force Notify
            logger.info("[TEST] Sending Test Notification (New)...")
            
            # Send Test Notification (New)
            await self.notifier.send_telegram(session, item, is_new=True, modified_reason="[TEST] 강제 알림 테스트")
            discord_thread_id = await self.notifier.send_discord(session, item, is_new=True, modified_reason="[TEST] 강제 알림 테스트")
            
            logger.info(f"[TEST] New Notification Sent! Thread ID: {discord_thread_id}")
            
            if discord_thread_id:
                logger.info("[TEST] Waiting 2s before sending update test...")
                await asyncio.sleep(2)
                
                logger.info("[TEST] Sending Test Notification (Update)...")
                # Simulate Update
                await self.notifier.send_discord(session, item, is_new=False, modified_reason="[TEST] 업데이트 테스트 (답글)", existing_thread_id=discord_thread_id)
                logger.info("[TEST] Update Notification Sent!")
            else:
                logger.warning("[TEST] Failed to get Thread ID, skipping update test.")
