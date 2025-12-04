import asyncio
import aiohttp
import hashlib
from typing import Dict, List
from core.config import settings
from core.logger import get_logger
from core.exceptions import (
    NetworkException,
    ScraperException,
)
import json
import os
from models.notice import Notice
from models.target import Target
from repositories.notice_repo import NoticeRepository
from services.ai_service import AIService
from services.notification_service import NotificationService
from services.file_service import FileService
from parsers.html_parser import HTMLParser
from core.performance import get_performance_monitor
from core import constants

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
        self.MAX_AI_SUMMARIES = constants.MAX_AI_SUMMARIES

        # Rate Limiting (Gemini 2.5 Flash: 10 RPM = 6 seconds per request)
        # Using 7s for safety margin + each notice has multiple AI calls
        self.AI_CALL_DELAY = constants.AI_CALL_DELAY  # 7 seconds between AI calls
        self.NOTICE_PROCESS_DELAY = constants.NOTICE_PROCESS_DELAY  # 0.5 seconds between each notice

        # Load Targets from JSON
        self.targets = self._load_targets()

    def _load_targets(self) -> List[Dict]:
        """
        Loads targets from resources/targets.json and validates them.
        """
        targets_path = os.path.join(os.path.dirname(__file__), "../resources/targets.json")
        if not os.path.exists(targets_path):
            logger.error(f"[SCRAPER] Targets file not found at {targets_path}")
            return []

        try:
            with open(targets_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            valid_targets = []
            for item in data:
                try:
                    target = Target(**item)
                    # Convert to dictionary format expected by ScraperService (with parser instance)
                    target_dict = target.model_dump()
                    target_dict["parser"] = HTMLParser(
                        target.list_selector,
                        target.title_selector,
                        target.link_selector,
                        target.content_selector
                    )
                    valid_targets.append(target_dict)
                except Exception as e:
                    logger.error(f"[SCRAPER] Invalid target configuration: {item.get('key', 'unknown')} - {e}")
            
            logger.info(f"[SCRAPER] Loaded {len(valid_targets)} targets from {targets_path}")
            return valid_targets

        except Exception as e:
            logger.error(f"[SCRAPER] Failed to load targets: {e}")
            return []

    def filter_targets(self, target_key: str):
        """
        Filters the targets list to only include the specified key.
        Useful for Matrix execution in GitHub Actions.
        """
        original_count = len(self.targets)
        self.targets = [t for t in self.targets if t["key"] == target_key]
        
        if not self.targets:
            logger.warning(f"[SCRAPER] Target '{target_key}' not found! Available keys: {[t['key'] for t in self.targets]}")
        else:
            logger.info(f"[SCRAPER] Filtered targets: {original_count} -> {len(self.targets)} (Target: {target_key})")

    def calculate_hash(self, notice: Notice) -> str:
        """Hash of Title + Content + Image + Attachments (name + URL + Size + ETag)"""
        # Include attachment name, url, size, etag to detect file replacements/updates
        sorted_atts = sorted(
            [
                f"{a.name}|{a.url}|{a.file_size or 0}|{a.etag or ''}"
                for a in notice.attachments
            ]
        )
        att_str = "".join(sorted_atts)

        # Include all image URLs (sorted for consistency)
        img_str = "|".join(sorted(notice.image_urls)) if notice.image_urls else ""

        # Include attachment text in hash
        att_text = notice.attachment_text or ""

        raw = f"{notice.title}{notice.content}{img_str}{att_str}{att_text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def process_menu_notice(self, session: aiohttp.ClientSession, notice: Notice):
        """
        Special handling for Dormitory Menu notices.
        """
        if not notice.image_urls:
            logger.warning(
                f"[MENU] Notice {notice.title} has no image, skipping menu extraction."
            )
            return

        logger.info(f"[MENU] Extracting menu from image: {notice.title}")

        # 1. AI Extraction (use first image)
        menu_data = await self.ai.extract_menu_from_image(notice.image_urls[0])
        if not menu_data or "raw_text" not in menu_data:
            logger.error("[MENU] Failed to extract menu text")
            return

        # 2. Save to DB (menus table)
        # TODO: Implement MenuRepository if needed, for now just logging
        logger.info(
            f"[MENU] Extracted: {menu_data['start_date']} ~ {menu_data['end_date']}"
        )

        # 3. Send & Pin to Telegram
        # Send Image
        # Send Text
        # Pin Text
        # Unpin Old
        await self.notifier.send_menu_notification(session, notice, menu_data)

    async def fetch_url(self, session: aiohttp.ClientSession, url: str) -> str:
        """Helper to fetch URL with error handling"""
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except asyncio.TimeoutError:
            raise NetworkException(f"Timeout fetching {url}", {"url": url})
        except aiohttp.ClientError as e:
            raise NetworkException(
                f"HTTP error fetching {url}", {"url": url, "error": str(e)}
            )
        except Exception as e:
            raise ScraperException(
                f"Unexpected error fetching {url}", {"url": url, "error": str(e)}
            )

    async def process_target(self, session: aiohttp.ClientSession, target: Dict):
        key = target["key"]
        monitor = get_performance_monitor()

        with monitor.measure("scrape_target", {"key": key}):
            logger.info(f"[SCRAPER] Scraping {key}...")

        try:
            html = await self.fetch_url(session, target["url"])
        except Exception as e:
            # Already wrapped in custom exceptions by fetch_url, but we might want to add key context
            # Re-raising with key context if needed, or just letting it bubble up
            # The original code raised NetworkException with key.
            # fetch_url raises NetworkException with url.
            # We can catch and re-raise or just let it be.
            # For consistency with original error messages:
            if isinstance(e, NetworkException):
                e.details["key"] = key
            raise e

        parser = target["parser"]
        items = parser.parse_list(html, key, target["base_url"])

        # IMPORTANT: Process oldest first (reverse chronological order)
        items.reverse()

        # Get already processed IDs
        processed_ids = self.repo.get_last_processed_ids(key, limit=1000)

        for item in items:
            is_new = item.article_id not in processed_ids
            old_hash = processed_ids.get(item.article_id)

            # Fetch detail
            try:
                detail_html = await self.fetch_url(session, item.url)
            except Exception as e:
                logger.warning(
                    f"[SCRAPER] Failed to fetch detail for {item.title}: {e}"
                )
                continue

            item = parser.parse_detail(detail_html, item)

            # Detect empty content (Allow if has attachments or images)
            has_media = bool(item.attachments or item.image_urls)
            if (not item.content or len(item.content.strip()) < 10) and not has_media:
                logger.warning(
                    f"[SCRAPER] Empty or very short content for '{item.title}' and no media. Skipping."
                )
                continue

            # Sanitize content (remove null bytes)
            if item.content:
                item.content = item.content.replace("\x00", "")
                item.content = item.content.strip()  # Normalize whitespace

            # --- SMART UPDATE CHECK ---
            should_process = True
            if not is_new:
                old_notice = self.repo.get_notice(key, item.article_id)
                if old_notice:
                    should_process = await self.should_process_article(
                        session, item, old_notice
                    )
                    if not should_process:
                        logger.info(f"[SCRAPER] No changes detected for '{item.title}'. Skipping.")
                        continue
                    else:
                        logger.info(f"[SCRAPER] Changes detected for '{item.title}'. Reprocessing.")

            # --- ATTACHMENT TEXT EXTRACTION & PREVIEW (Tier 1) ---
            if item.attachments:
                extracted_texts = []
                preview_count = 0
                MAX_PREVIEWS = constants.MAX_PREVIEWS  # Increased limit

                # Limit to first 10 attachments for processing
                for att in item.attachments[:10]:
                    ext = att.name.split(".")[-1].lower() if "." in att.name else ""

                    # --- METADATA CAPTURE (For Smart Update) ---
                    # We need to capture file_size and etag for ALL attachments, 
                    # not just the ones we extract text from.
                    headers = {
                        "Referer": item.url,
                        "User-Agent": settings.USER_AGENT,
                    }
                    file_data = None
                    
                    # Try to download (or at least HEAD) to get metadata
                    # If we need to extract text/preview, we download fully.
                    # If not, we still need metadata.
                    
                    needs_processing = ext in ["hwp", "hwpx", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"]
                    
                    try:
                        if needs_processing:
                            logger.info(f"[SCRAPER] Downloading attachment for processing: {att.name}")
                            async with session.get(att.url, headers=headers) as resp:
                                resp.raise_for_status()
                                file_data = await resp.read()
                                
                                # Capture Metadata
                                att.file_size = int(resp.headers.get("Content-Length", 0)) or len(file_data)
                                att.etag = resp.headers.get("ETag")
                        else:
                            # Just get metadata via HEAD if we haven't already
                            # (Actually, should_process_article might have done it, but we need to save it to DB)
                            # Wait, should_process_article does NOT update the 'item' object's attachments with metadata.
                            # It just checks. We need to populate 'att.file_size' and 'att.etag' here so it gets saved to DB.
                            
                            # Optimization: If we already have metadata from should_process_article (we don't pass it back),
                            # we need to fetch it.
                            # Let's do a HEAD request here if file_data is None.
                             async with session.head(att.url, headers=headers, timeout=5) as resp:
                                att.file_size = int(resp.headers.get("Content-Length", 0))
                                att.etag = resp.headers.get("ETag")

                    except Exception as e:
                        logger.warning(f"[SCRAPER] Failed to capture metadata/download for {att.name}: {e}")
                        file_data = None

                    # 1. Text Extraction (HWP, PDF)
                    if file_data and ext in ["hwp", "hwpx", "pdf"]:
                            text = self.file_service.extract_text(file_data, att.name)
                            if text:
                                text = text.strip()
                                if len(text) > 100:
                                    extracted_texts.append(
                                        f"--- 첨부파일: {att.name} ---\n{text[:3000]}..."
                                    )
                                    logger.info(
                                        f"[SCRAPER] Extracted {len(text)} chars from {att.name}"
                                    )

                    # 2. Document Preview Generation (PDF, HWP, DOCX, etc.)
                    supported_preview_exts = [
                        "pdf",
                        "hwp",
                        "hwpx",
                        "doc",
                        "docx",
                        "xls",
                        "xlsx",
                        "ppt",
                        "pptx",
                    ]
                    
                    if not file_data:
                        logger.warning(f"[SCRAPER] Skipping preview for {att.name}: No file data")
                    elif ext not in supported_preview_exts:
                        logger.debug(f"[SCRAPER] Skipping preview for {att.name}: Unsupported extension {ext}")
                    elif preview_count >= MAX_PREVIEWS:
                        logger.warning(f"[SCRAPER] Skipping preview for {att.name}: Preview limit ({MAX_PREVIEWS}) reached")
                    else:
                        logger.info(
                            f"[SCRAPER] Generating preview for {att.name}..."
                        )
                        # Generate up to 10 pages
                        preview_images = (
                            self.file_service.generate_preview_images(
                                file_data, att.name, max_pages=20
                            )
                        )
                        if preview_images:
                            att.preview_images = (
                                preview_images  # Store list in Attachment model
                            )
                            preview_count += 1
                            logger.info(
                                f"[SCRAPER] Preview generated for {att.name} ({len(preview_images)} pages)"
                            )
                        else:
                            logger.warning(f"[SCRAPER] Preview generation returned empty for {att.name}")

                    # Small delay to prevent rate limiting
                    await asyncio.sleep(0.5)

                if extracted_texts:
                    # Save extracted text to attachment_text field instead of appending to content
                    item.attachment_text = "\n\n".join(extracted_texts)
            # -------------------------------------------

            logger.info(
                f"[SCRAPER] Content length for '{item.title}': {len(item.content)}"
            )
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
                item.category = "일반"
                item.summary = "초기화 모드로 저장됨 (AI 요약 없음)"
                item.embedding = None

                # Save to DB only
                self.repo.upsert_notice(item)
                continue
            # -----------------------

            # --- NO-AI MODE LOGIC ---
            if self.no_ai_mode:
                logger.info(f"[NO-AI] Skipping AI analysis for: {item.title}")
                item.category = "일반"
                item.summary = "AI 분석 건너뜀 (No-AI Mode)"
                item.embedding = None
            else:
                # AI Analysis with rate limiting
                if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                    # Skip AI if content is too short (use content as summary)
                    # BUT if we extracted attachment text, content might be long now!
                        logger.info(
                            f"[SCRAPER] Starting AI analysis ({self.ai_summary_count + 1}/{self.MAX_AI_SUMMARIES})..."
                        )

                        # 1. Analyze content (Wait BEFORE first call)
                        logger.info(
                            f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before analyze_notice..."
                        )
                        await asyncio.sleep(self.AI_CALL_DELAY)

                        with monitor.measure(
                            "ai_analysis", {"type": "summary", "title": item.title}
                        ):
                            # Combine content and attachment text for AI analysis
                            full_text = (
                                f"{item.content}\n\n{item.attachment_text or ''}"
                            )
                            analysis = await self.ai.analyze_notice(
                                full_text,
                                site_key=item.site_key,
                                title=item.title,
                                author=item.author or "",
                            )

                        item.category = analysis.get("category", "일반")
                        item.tags = analysis.get(
                            "tags", []
                        )  # NEW: Store AI-selected tags
                        
                        # Enhanced Logging for Debugging
                        logger.info(
                            f"[SCRAPER] AI Analysis Result for '{item.title}':\n"
                            f"  - Category: {item.category}\n"
                            f"  - Tags: {item.tags}\n"
                            f"  - Deadline: {analysis.get('deadline')}\n"
                            f"  - Target Dept: {analysis.get('target_dept')}\n"
                            f"  - Eligibility: {analysis.get('eligibility')}"
                        )
                        
                        # Handle Short Content Summary (Short Article / 단신)
                        # If content is short (< 100 chars) and no meaningful attachment text
                        content_len = len(item.content.strip())
                        att_text_len = len((item.attachment_text or "").strip())
                        
                        logger.info(f"[SCRAPER] Content Len: {content_len}, Att Text Len: {att_text_len}")

                        if content_len < constants.SHORT_NOTICE_CONTENT_LENGTH and att_text_len < constants.SHORT_NOTICE_ATTACHMENT_LENGTH:
                             item.summary = f"[단신] {item.content.strip()}"
                             logger.info(f"[SCRAPER] Treated as Short Article (단신)")
                        else:
                             item.summary = analysis.get("summary", item.content[:100])

                        # Tier 2: Enhanced Metadata
                        item.deadline = analysis.get("deadline")
                        item.eligibility = analysis.get("eligibility", [])
                        item.start_date = analysis.get("start_date")
                        item.end_date = analysis.get("end_date")
                        item.target_grades = analysis.get("target_grades", [])
                        item.target_dept = analysis.get("target_dept")

                        # 2. Get embedding (Wait BEFORE second call)
                        logger.info(
                            f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_embedding..."
                        )
                        await asyncio.sleep(self.AI_CALL_DELAY)

                        item.embedding = await self.ai.get_embedding(
                            f"{item.title}\n{item.summary}"
                        )
                        self.ai_summary_count += 1

                        logger.info(
                            f"[SCRAPER] AI complete. Quota: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}"
                        )
                else:
                    logger.warning("[SCRAPER] AI limit reached. Skipping AI analysis.")
                    item.category = "일반"
                    item.summary = item.content[:100] + " (AI 한도 도달)"
                    item.embedding = []

            # AI Diff for Modified
            if is_modified:
                old_notice = self.repo.get_notice(key, item.article_id)
                changes = {}
                if old_notice:
                    changes = await self.detect_modifications(item, old_notice)

                # [FIX] If hash changed but no actual changes detected (e.g. hash algo update),
                # update DB but do NOT notify.
                if not changes:
                    logger.info(
                        f"[SCRAPER] Hash mismatch but no content changes detected for '{item.title}'. Updating hash only."
                    )
                    self.repo.upsert_notice(item)
                    continue

                item.change_details = changes

                # Construct readable reason
                reasons = []
                if "title" in changes:
                    reasons.append("제목 변경")
                if "content" in changes:
                    reasons.append("내용 변경")
                if "attachment_text" in changes:
                    reasons.append("첨부파일 내용 변경")
                if "image" in changes:
                    reasons.append("이미지 변경")
                if "attachments" in changes:
                    reasons.append(f"첨부파일 목록 변경 ({changes['attachments']})")

                modified_reason = ", ".join(reasons) if reasons else "내용 변경됨"

            # Save to DB
            notice_id = self.repo.upsert_notice(item)

            if notice_id:
                # Notify
                existing_message_id = None
                if is_modified and "old_notice" in locals() and old_notice:
                    existing_message_id = old_notice.message_ids.get("telegram")

                msg_id = await self.notifier.send_telegram(
                    session, item, is_new, modified_reason, existing_message_id=existing_message_id
                )
                if msg_id:
                    self.repo.update_message_ids(notice_id, "telegram", msg_id)

                # Discord Notification
                # Check for existing thread ID if modified
                existing_thread_id = None
                if is_modified and "old_notice" in locals() and old_notice:
                    existing_thread_id = old_notice.discord_thread_id

                discord_thread_id = await self.notifier.send_discord(
                    session,
                    item,
                    is_new,
                    modified_reason,
                    existing_thread_id=existing_thread_id,
                )

                if discord_thread_id:
                    self.repo.update_discord_thread_id(notice_id, discord_thread_id)

            # Small delay between notices
            await asyncio.sleep(self.NOTICE_PROCESS_DELAY)

    async def detect_modifications(self, item: Notice, old_notice: Notice) -> Dict:
        """Detect changes between old and new notice"""
        changes = {}
        if old_notice.title != item.title:
            changes["title"] = f"'{old_notice.title}' -> '{item.title}'"

        if old_notice.content != item.content:
            # Pre-check: Ignore whitespace-only changes
            if old_notice.content.strip() == item.content.strip():
                logger.info(
                    f"[SCRAPER] Content change detected but is whitespace-only for '{item.title}'. Ignoring."
                )
            else:
                # Store old and new content for detailed diff display
                changes["old_content"] = old_notice.content
                changes["new_content"] = item.content

                if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                    logger.info(
                        f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_diff_summary..."
                    )
                    await asyncio.sleep(self.AI_CALL_DELAY)
                    diff_summary = await self.ai.get_diff_summary(
                        old_notice.content, item.content
                    )

                    # Check for "No Change" response
                    if (
                        diff_summary in ["NO_CHANGE", "변동사항 없음", "내용 변경 없음"]
                        or "내용 변화는 없습니다" in diff_summary
                        or "변경사항이 없습니다" in diff_summary
                    ):
                        logger.info(
                            f"[SCRAPER] AI reported no semantic change for '{item.title}'. Ignoring content change."
                        )
                        # Remove content change from tracking
                        if "old_content" in changes:
                            del changes["old_content"]
                        if "new_content" in changes:
                            del changes["new_content"]
                    else:
                        changes["content"] = diff_summary
                        self.ai_summary_count += 1  # Count diff as an AI call
                else:
                    changes["content"] = "내용 변경됨 (AI 한도 초과)"

        # Attachment Text Change Detection
        if (old_notice.attachment_text or "").strip() != (
            item.attachment_text or ""
        ).strip():
            changes["attachment_text"] = "첨부파일 내용 변경됨 (상세 내용 생략)"

        # Image change detection (compare sets of URLs)
        old_imgs = set(old_notice.image_urls) if old_notice.image_urls else set()
        new_imgs = set(item.image_urls) if item.image_urls else set()
        if old_imgs != new_imgs:
            added_imgs = new_imgs - old_imgs
            removed_imgs = old_imgs - new_imgs
            img_changes = []
            if added_imgs:
                img_changes.append(f"추가: {len(added_imgs)}개")
            if removed_imgs:
                img_changes.append(f"제거: {len(removed_imgs)}개")
            changes["image"] = ", ".join(img_changes)

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
            if added:
                att_changes.append(f"추가: {', '.join(added)}")
            if removed:
                att_changes.append(f"제거: {', '.join(removed)}")
            if url_changed:
                att_changes.append(f"재업로드: {', '.join(url_changed)}")
            changes["attachments"] = ", ".join(att_changes)

        return changes

    async def should_process_article(
        self, session: aiohttp.ClientSession, new_item: Notice, old_item: Notice
    ) -> bool:
        """
        Determines if an article should be processed (downloaded/updated)
        by comparing metadata and performing HEAD requests for attachments.
        """
        # 1. Metadata Check
        if new_item.title != old_item.title:
            logger.info(f"[SMART-UPDATE] Title changed: {new_item.title}")
            return True
        if new_item.content != old_item.content:
            logger.info(f"[SMART-UPDATE] Content changed: {new_item.title}")
            return True

        # Compare attachment counts
        if len(new_item.attachments) != len(old_item.attachments):
            logger.info(f"[SMART-UPDATE] Attachment count changed: {new_item.title}")
            return True

        # Compare attachment URLs
        new_urls = {a.url for a in new_item.attachments}
        old_urls = {a.url for a in old_item.attachments}
        if new_urls != old_urls:
            logger.info(f"[SMART-UPDATE] Attachment URLs changed: {new_item.title}")
            return True

        # 2. HEAD Request Check (for each attachment)
        old_att_map = {a.url: a for a in old_item.attachments}

        for new_att in new_item.attachments:
            old_att = old_att_map.get(new_att.url)
            if not old_att:
                return True  # Should be caught by URL check, but safe guard

            # Send HEAD request
            try:
                async with session.head(new_att.url, timeout=5) as resp:
                    # Fail-safe: if not 200, assume changed/error -> Process
                    if resp.status != 200:
                        logger.warning(
                            f"[SMART-UPDATE] HEAD request failed ({resp.status}) for {new_att.name}. Assuming changed."
                        )
                        return True

                    remote_size = int(resp.headers.get("Content-Length", 0))
                    remote_etag = resp.headers.get("ETag")

                    # Priority: ETag -> Size
                    if remote_etag:
                        if not old_att.etag or remote_etag != old_att.etag:
                            logger.info(
                                f"[SMART-UPDATE] ETag mismatch/missing for {new_att.name}. Old: {old_att.etag}, New: {remote_etag}"
                            )
                            return True
                    elif remote_size > 0:
                        if not old_att.file_size or remote_size != old_att.file_size:
                            logger.info(
                                f"[SMART-UPDATE] File size mismatch/missing for {new_att.name}. Old: {old_att.file_size}, New: {remote_size}"
                            )
                            return True
                    else:
                        # Both missing from server, default to True (download)
                        logger.warning(
                            f"[SMART-UPDATE] No ETag or Content-Length for {new_att.name}. Forcing update."
                        )
                        return True

            except Exception as e:
                logger.warning(
                    f"[SMART-UPDATE] HEAD request exception for {new_att.name}: {e}. Assuming changed."
                )
                return True

        return False

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)

        # Complete browser headers required by YU site
        headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            monitor = get_performance_monitor()

            with monitor.measure("full_scrape_run"):
                logger.info(
                    f"[SCRAPER] Processing {len(self.targets)} targets sequentially..."
                )

                for target in self.targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] Target {target['key']} failed: {e}")

            logger.info(
                f"[SCRAPER] Complete. Total AI calls: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}"
            )
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
            if t["base_url"] in test_url or t["url"] in test_url:
                target = t
                break

        if not target:
            # Fallback to generic parser if no target matches
            logger.warning(
                f"[TEST] No matching target found for {test_url}. Using generic parser."
            )
            target = self.targets[0]  # Use first as default

        parser = target["parser"]

        # 2. Fetch Content
        timeout = aiohttp.ClientTimeout(total=30)

        # Use same complete browser headers as main scraper
        headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": "https://www.yu.ac.kr/",
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
            soup = BeautifulSoup(html, "html.parser")

            # Check if this URL matches any target's base_url
            matched_target = next(
                (
                    t
                    for t in self.targets
                    if t["url"] in test_url or test_url in t["url"]
                ),
                None,
            )

            if matched_target:
                # Try to parse as list first
                logger.info(
                    f"[TEST] URL matches target '{matched_target['key']}'. Checking if it's a list page..."
                )
                items = matched_target["parser"].parse_list(
                    html, matched_target["key"], matched_target["base_url"]
                )

                if items:
                    logger.info(
                        f"[TEST] Detected list page with {len(items)} items. Picking the first one for testing."
                    )
                    first_item = items[0]
                    logger.info(
                        f"[TEST] Redirecting test to: {first_item.title} ({first_item.url})"
                    )

                    # Recursively call run_test with the item's URL
                    # But we need to be careful about infinite recursion if parsing fails
                    if first_item.url != test_url:
                        await self.run_test(first_item.url)
                        return
                    else:
                        logger.warning(
                            "[TEST] First item URL is same as list URL. Proceeding as detail page."
                        )

            # 4. Parse (Simulate Item)
            # (Proceed with existing detail parsing logic)

            # Try to find title with YU-specific selectors first
            title = "Test Notification"
            title_selectors = [
                ".b-title-box",  # YU main title container
                ".b-view-title",  # YU view title
                ".view-title",  # Generic view title
                ".board-view-title",  # Board view title
                "h1",  # Fallback to h1
                "h2",  # Fallback to h2
                "title",  # Last resort: page title
            ]

            for selector in title_selectors:
                title_elem = soup.select_one(selector)
                if title_elem:
                    title_text = title_elem.get_text(strip=True)
                    # Skip if it's just whitespace or too short
                    if title_text and len(title_text) > 3:
                        # Clean up title
                        # 1. For <title> tag, remove site name suffix
                        if selector == "title" and "|" in title_text:
                            title_text = title_text.split("|")[0].strip()

                        # 2. Remove common markers (N=New, HOT, UP, etc.)
                        # These appear at the end of titles on YU notice boards
                        import re

                        # Remove single letter markers at the end (N, U, etc.)
                        title_text = re.sub(r"\s*[NUHOT]+\s*$", "", title_text)
                        # Remove "New" marker
                        title_text = re.sub(
                            r"\s*New\s*$", "", title_text, flags=re.IGNORECASE
                        )

                        title = title_text.strip()
                        logger.info(
                            f"[TEST] Found title with selector '{selector}': {title}"
                        )
                        break

            # Create dummy item
            # If no target matched, use first target as fallback (for test mode)
            test_site_key = (
                target["key"]
                if target
                else (
                    matched_target["key"] if matched_target else self.targets[0]["key"]
                )
            )
            item = Notice(
                site_key=test_site_key,
                article_id="test_id",
                title=title,
                url=test_url,
                published_at=None,
            )

            # Use parser to fill content and attachments
            item = parser.parse_detail(html, item)

            # --- ATTACHMENT TEXT EXTRACTION (Test Mode) ---
            if item.attachments:
                logger.info(
                    f"[TEST] Found {len(item.attachments)} attachments. Attempting extraction..."
                )
                extracted_texts = []
                for att in item.attachments[:10]:
                    ext = att.name.split(".")[-1].lower().strip() if "." in att.name else ""
                    logger.info(f"[TEST] Processing attachment: {att.name} (ext: '{ext}')")
                    
                    # Check if we need to download (for extraction OR preview)
                    needs_extraction = ext in ["hwp", "hwpx", "pdf"]
                    supported_preview_exts = [
                        "pdf",
                        "hwp",
                        "hwpx",
                        "doc",
                        "docx",
                        "xls",
                        "xlsx",
                        "ppt",
                        "pptx",
                    ]
                    needs_preview = ext in supported_preview_exts
                    
                    if needs_extraction or needs_preview:
                        logger.info(f"[TEST] Downloading {att.name}...")
                        headers = {
                            "Referer": item.url,
                            "User-Agent": settings.USER_AGENT,
                        }
                        file_data = await self.file_service.download_file(
                            session, att.url, headers=headers
                        )

                        if file_data:
                            # 1. Extraction
                            if needs_extraction:
                                text = self.file_service.extract_text(file_data, att.name)
                                if text:
                                    text = text.strip()
                                    if len(text) > 50:
                                        extracted_texts.append(
                                            f"--- 첨부파일: {att.name} ---\n{text[:1000]}..."
                                        )
                                        logger.info(
                                            f"[TEST] ✅ Extracted {len(text)} chars from {att.name}"
                                        )
                                        logger.info(f"[TEST] Preview: {text[:200]}")
                                    else:
                                        logger.warning(
                                            "[TEST] Extracted text too short or empty."
                                        )
                                else:
                                    logger.warning(
                                        "[TEST] Extraction returned empty string."
                                    )
                            
                            # 2. Preview Generation
                            if needs_preview:
                                logger.info(f"[TEST] Generating preview for {att.name}...")
                                preview_images = self.file_service.generate_preview_images(
                                    file_data, att.name, max_pages=20
                                )
                                if preview_images:
                                    att.preview_images = preview_images
                                    logger.info(
                                        f"[TEST] ✅ Preview generated: {len(preview_images)} pages"
                                    )
                                else:
                                    logger.warning("[TEST] Preview generation failed (returned empty).")
                        else:
                            logger.error("[TEST] Download failed.")

                if extracted_texts:
                    item.content += "\n\n" + "\n".join(extracted_texts)
                    logger.info(
                        f"[TEST] Content updated with attachment text. New length: {len(item.content)}"
                    )
            # ----------------------------------------------

            # --- AI ANALYSIS (Test Mode) ---
            logger.info("[TEST] Starting AI analysis for verification...")
            analysis = await self.ai.analyze_notice(
                item.content,
                site_key=item.site_key,
                title=item.title,
                author=item.author or "",
            )

            item.category = analysis.get("category", "일반")
            item.tags = analysis.get("tags", [])  # NEW: Store AI-selected tags
            
            # Handle Short Content Summary (Short Article / 단신)
            # If content is short (< 100 chars) and no meaningful attachment text
            content_len = len(item.content.strip())
            # Note: In run_test, we appended extracted text to item.content, so we need to be careful.
            # But wait, lines 948-949: item.content += "\n\n" + ...
            # So item.content ALREADY includes attachment text if extracted.
            # But for images, it won't have attachment text.
            # So checking len(item.content) is correct.
            # However, to match process_target logic exactly, we should check if it was appended?
            # Actually, process_target keeps attachment_text separate until AI analysis, but here run_test merges it.
            # Let's just check the total length. If it's short, it's short.
            
            if len(item.content.strip()) < 100:
                 item.summary = f"[단신] {item.content.strip()}"
                 logger.info(f"[TEST] Treated as Short Article (단신) - Len: {len(item.content.strip())}")
            else:
                 item.summary = analysis.get("summary", item.content[:100])
            item.deadline = analysis.get("deadline")
            item.eligibility = analysis.get("eligibility", [])
            item.start_date = analysis.get("start_date")
            item.end_date = analysis.get("end_date")
            item.target_grades = analysis.get("target_grades", [])
            item.target_dept = analysis.get("target_dept")

            logger.info("[TEST] AI Result:")
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
            await self.notifier.send_telegram(
                session, item, is_new=True, modified_reason="[TEST] 강제 알림 테스트"
            )
            discord_thread_id = await self.notifier.send_discord(
                session, item, is_new=True, modified_reason="[TEST] 강제 알림 테스트"
            )

            logger.info(f"[TEST] New Notification Sent! Thread ID: {discord_thread_id}")

            if discord_thread_id:
                logger.info("[TEST] Waiting 2s before sending update test...")
                await asyncio.sleep(2)

                logger.info("[TEST] Sending Test Notification (Update)...")
                # Simulate Update
                await self.notifier.send_discord(
                    session,
                    item,
                    is_new=False,
                    modified_reason="[TEST] 업데이트 테스트 (답글)",
                    existing_thread_id=discord_thread_id,
                )
                logger.info("[TEST] Update Notification Sent!")
            else:
                logger.warning("[TEST] Failed to get Thread ID, skipping update test.")
