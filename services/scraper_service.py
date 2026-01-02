import asyncio
import aiohttp
import hashlib
from typing import Dict, List, Optional
from core.config import settings
from core.logger import get_logger
from core.exceptions import NetworkException, ScraperException
from core.interfaces import INotificationService, IFileService, INoticeRepository
import json
import os
from models.notice import Notice
from models.target import Target
from repositories.notice_repo import NoticeRepository
from services.notification_service import NotificationService
from services.file_service import FileService
from services.file_service import FileService
from parsers.html_parser import HTMLParser
from parsers.eoullim_parser import EoullimParser
from services.auth_service import AuthService
from core.performance import get_performance_monitor
from core import constants

# New Components
from services.scraper.fetcher import NoticeFetcher
from services.scraper.parser import NoticeParser
from services.scraper.analyzer import ContentAnalyzer

logger = get_logger(__name__)


class ScraperService:
    """
    Main scraper service that orchestrates notice fetching, parsing, and notification.
    Supports dependency injection for easier testing and extensibility.
    """

    def __init__(
        self,
        init_mode: bool = False,
        no_ai_mode: bool = False,
        # Dependency Injection - Optional, defaults to real implementations
        notifier: Optional[INotificationService] = None,
        file_service: Optional[IFileService] = None,
        repo: Optional[INoticeRepository] = None,
        fetcher: Optional[NoticeFetcher] = None,
        parser: Optional[NoticeParser] = None,
        analyzer: Optional[ContentAnalyzer] = None,
    ):
        # Inject or create default instances
        self.repo = repo or NoticeRepository()
        self.notifier = notifier or NotificationService()
        self.file_service = file_service or FileService()
        
        # New Components
        self.fetcher = fetcher or NoticeFetcher()
        self.fetcher = fetcher or NoticeFetcher()
        self.parser = parser or NoticeParser() # Note: This seems to be a service/manager, not the actual parser instance. 
        # Wait, self.parser is assigned NoticeParser() in line 53.
        # But _load_targets creates HTMLParser() instances and puts them in target dict.
        # The code uses self.parser.parse_list (line 152) which calls HTMLParser methods?
        # No, line 22 imports `NoticeParser` from `services.scraper.parser`.
        # That `NoticeParser` service likely delegates to the parser instance in target dict.
        # Let's check `services/scraper/parser.py` content to match calling convention.
        # Assuming `target['parser']` is the actual parser instance (HTMLParser or EoullimParser).
        
        self.analyzer = analyzer or ContentAnalyzer(no_ai_mode=no_ai_mode)
        self.auth_service = AuthService()
        
        self.init_mode = init_mode
        self.no_ai_mode = no_ai_mode
        
        self.NOTICE_PROCESS_DELAY = constants.NOTICE_PROCESS_DELAY

        # Load Targets
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
                    target_dict = target.model_dump()
                    # Keep HTMLParser instance for Strategy Pattern
                    if target.key.startswith("eoullim_"):
                        target_dict["parser"] = EoullimParser(
                            target.list_selector,
                            target.title_selector,
                            target.link_selector,
                            target.content_selector
                        )
                    else:
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
        """Filters the targets list to only include the specified key."""
        original_count = len(self.targets)
        self.targets = [t for t in self.targets if t["key"] == target_key]
        
        if not self.targets:
            logger.warning(f"[SCRAPER] Target '{target_key}' not found! Available keys: {[t['key'] for t in self.targets]}")
        else:
            logger.info(f"[SCRAPER] Filtered targets: {original_count} -> {len(self.targets)} (Target: {target_key})")

    def calculate_hash(self, notice: Notice) -> str:
        """Hash of Title + Content + Image + Attachments (name + URL + Size + ETag)"""
        sorted_atts = sorted(
            [
                f"{a.name}|{a.url}|{a.file_size or 0}|{a.etag or ''}"
                for a in notice.attachments
            ]
        )
        att_str = "".join(sorted_atts)
        img_str = "|".join(sorted(notice.image_urls)) if notice.image_urls else ""
        att_text = notice.attachment_text or ""
        raw = f"{notice.title}{notice.content}{img_str}{att_str}{att_text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def run(self) -> bool:
        """
        Runs the scraper for all loaded targets.
        Returns: True if all targets succeeded, False if any failed.
        """
        # Authenticate if needed
        if any(t["key"].startswith("eoullim_") for t in self.targets):
            cookies = await self.auth_service.get_eoullim_cookies()
        
        session = await self.fetcher.create_session()
        
        if "cookies" in locals() and cookies:
            self.fetcher.set_cookies(session, cookies)
            
        success = True
        
        async with session:
            monitor = get_performance_monitor()
            with monitor.measure("full_scrape_run"):
                logger.info(f"[SCRAPER] Processing {len(self.targets)} targets sequentially...")
                for target in self.targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] Target {target['key']} failed: {e}")
                        success = False
                        # Send Immediate Dev Alert
                        from core.error_notifier import get_error_notifier, ErrorSeverity
                        await get_error_notifier().send_critical_error(
                            f"Target '{target['key']}' failed during scraping loop",
                            exception=e,
                            context={"key": target["key"]},
                            severity=ErrorSeverity.ERROR
                        )
            
            logger.info(f"[SCRAPER] Complete. Success: {success}")
            monitor.log_summary()
            return success

    async def process_target(self, session: aiohttp.ClientSession, target: Dict):
        key = target["key"]
        monitor = get_performance_monitor()

        with monitor.measure("scrape_target", {"key": key}):
            logger.info(f"[SCRAPER] Scraping {key}...")

        try:
            html = await self.fetcher.fetch_url(session, target["url"])
        except NetworkException as e:
            e.details["key"] = key
            raise e

        # Use NoticeParser
        items = self.parser.parse_list(target["parser"], html, key, target["base_url"])
        processed_ids = self.repo.get_last_processed_ids(key, limit=1000)

        for item in items:
            is_new = item.article_id not in processed_ids
            old_hash = processed_ids.get(item.article_id)

            # Fetch detail
            try:
                detail_html = await self.fetcher.fetch_url(session, item.url)
            except Exception as e:
                logger.warning(f"[SCRAPER] Failed to fetch detail for {item.title}: {e}")
                continue

            # Parse detail
            item = self.parser.parse_detail(target["parser"], detail_html, item)

            # Detect empty content
            has_media = bool(item.attachments or item.image_urls)
            if (not item.content or len(item.content.strip()) < 10) and not has_media:
                logger.warning(f"[SCRAPER] Empty or very short content for '{item.title}' and no media. Skipping.")
                continue

            # Smart Update Check
            should_process = True
            if not is_new:
                old_notice = self.repo.get_notice(key, item.article_id)
                if old_notice:
                    should_process = await self.should_process_article(session, item, old_notice)
                    if not should_process:
                        logger.info(f"[SCRAPER] No changes detected for '{item.title}'. Skipping.")
                        continue
                    else:
                        logger.info(f"[SCRAPER] Changes detected for '{item.title}'. Reprocessing.")

            # Attachment Processing (Text & Preview)
            if item.attachments:
                await self.process_attachments(session, item)

            # Hash Calculation
            current_hash = self.calculate_hash(item)
            item.content_hash = current_hash

            is_modified = False
            modified_reason = ""

            if not is_new:
                if old_hash == current_hash:
                    continue
                else:
                    is_modified = True
                    modified_reason = "내용 또는 제목 변경됨"

            logger.info(f"Processing {'New' if is_new else 'Modified'}: {item.title}")

            # Init Mode
            if self.init_mode:
                logger.info(f"[INIT] Seeding database with: {item.title}")
                item.category = "일반"
                item.summary = "초기화 모드로 저장됨 (AI 요약 없음)"
                item.embedding = None
                self.repo.upsert_notice(item)
                continue

            # AI Analysis
            if key == "dormitory_menu":
                # Skip AI for menu, force tags
                item.category = "식단"
                item.tags = ["기숙사"]
                item.summary = "기숙사 식단표입니다."

            else:
                # OPTIMIZATION: If content is identical to old_notice, reuse AI results to save tokens
                ai_skipped = False
                if not is_new and "old_notice" in locals() and old_notice:
                    # Clean comparison (ignore whitespace differences)
                    if (old_notice.content or "").strip() == (item.content or "").strip():
                         logger.info(f"[SCRAPER] Content is identical to previous version. Reusing AI metadata for '{item.title}'.")
                         item.summary = old_notice.summary
                         item.category = old_notice.category
                         item.tags = old_notice.tags
                         item.embedding = old_notice.embedding
                         ai_skipped = True
                
                if not ai_skipped:
                    item = await self.analyzer.analyze_notice(item)
                
                # Force dormitory tag for dormitory_notice
                if key == "dormitory_notice":
                    if "기숙사" not in item.tags:
                        item.tags.insert(0, "기숙사")

            # Diff for Modified
            changes = None
            if is_modified:
                old_notice = self.repo.get_notice(key, item.article_id)
                changes = {}
                if old_notice:
                    changes = await self.detect_modifications(item, old_notice)
                
                if not changes:
                    logger.info(f"[SCRAPER] Hash mismatch but no content changes detected for '{item.title}'. Updating hash only.")
                    self.repo.upsert_notice(item)
                    continue
                
                item.change_details = changes
                # Construct readable reason
                reasons = []
                if "title" in changes: reasons.append("제목 변경")
                if "content" in changes: reasons.append("내용 변경")
                if "attachment_text" in changes: reasons.append("첨부파일 내용 변경")
                if "image" in changes: reasons.append("이미지 변경")
                if "attachments" in changes: reasons.append(f"첨부파일 목록 변경 ({changes['attachments']})")
                modified_reason = ", ".join(reasons) if reasons else "내용 변경됨"

            # Save to DB
            notice_id = self.repo.upsert_notice(item)

            if notice_id:
                # Notify
                existing_message_id = None
                if is_modified and "old_notice" in locals() and old_notice:
                    existing_message_id = old_notice.message_ids.get("telegram")

                msg_id = await self.notifier.send_telegram(
                    session, item, is_new, modified_reason, existing_message_id=existing_message_id, changes=changes
                )
                if msg_id:
                    self.repo.update_message_ids(notice_id, "telegram", msg_id)

                # Discord
                existing_thread_id = None
                if is_modified and "old_notice" in locals() and old_notice:
                    existing_thread_id = old_notice.discord_thread_id

                discord_thread_id = await self.notifier.send_discord(
                    session, item, is_new, modified_reason, existing_thread_id=existing_thread_id, changes=changes
                )
                if discord_thread_id:
                    self.repo.update_discord_thread_id(notice_id, discord_thread_id)
            
            await asyncio.sleep(self.NOTICE_PROCESS_DELAY)

    async def process_attachments(self, session: aiohttp.ClientSession, item: Notice):
        """
        Downloads attachments, extracts text, and generates previews.
        """
        extracted_texts = []
        preview_count = 0
        MAX_PREVIEWS = constants.MAX_PREVIEWS

        # Prepare tasks for parallel processing
        # Limit concurrency to 2 to prevent CPU spike (Playwright is heavy)
        semaphore = asyncio.Semaphore(2)
        
        async def _process_att(att, item_url, session):
            async with semaphore:
                try:
                    ext = att.name.split(".")[-1].lower() if "." in att.name else ""
                    needs_processing = ext in ["hwp", "hwpx", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"]
                    
                    file_data = None
                    if needs_processing:
                        logger.info(f"[SCRAPER] Downloading attachment for processing: {att.name}")
                        file_data = await self.fetcher.download_file(session, att.url, item_url)
                        if file_data:
                            att.file_size = len(file_data)
                    else:
                        # Just metadata
                        meta = await self.fetcher.fetch_file_head(session, att.url, item_url)
                        att.file_size = meta["content_length"]
                        att.etag = meta["etag"]

                    if file_data:
                        # Text Extraction
                        text_result = None
                        if ext in ["hwp", "hwpx", "pdf"]:
                            text = self.file_service.extract_text(file_data, att.name)
                            if text and len(text.strip()) > 100:
                                text_result = f"--- 첨부파일: {att.name} ---\n{text.strip()[:3000]}..."

                        # Preview Generation
                        preview_result = None
                        # Check remaining preview slots (not strictly thread-safe but close enough for this)
                        # We gather results and check count later ideally, but let's just generate all and limit usage content-side?
                        # Or better: generate usually only first few matter.
                        # Let's just generate for all processed files and cap it at assignment time.
                        preview_images = self.file_service.generate_preview_images(file_data, att.name, max_pages=20)
                        if preview_images:
                            preview_result = preview_images
                            
                        return text_result, preview_result
                        
                except Exception as e:
                    logger.warning(f"[SCRAPER] Failed to process attachment {att.name}: {e}")
                return None, None

        # Create tasks
        tasks = [_process_att(att, item.url, session) for att in item.attachments[:10]]
        results = await asyncio.gather(*tasks)

        # Apply results
        preview_count = 0
        MAX_PREVIEWS = constants.MAX_PREVIEWS
        
        for i, (text_res, preview_res) in enumerate(results):
            if text_res:
                extracted_texts.append(text_res)
            
            att = item.attachments[i]
            if preview_res:
                # Assign previews if under limit
                if preview_count < MAX_PREVIEWS:
                    att.preview_images = preview_res
                    preview_count += 1
                else:
                    # Optional: Store but don't display? Or just discard to save memory.
                    # Current logic was: generate IF under limit. 
                    # Parallel logic: Generate ALL, then pick. 
                    # This uses more CPU/Memory but is faster wall-clock.
                    pass


        if extracted_texts:
            item.attachment_text = "\n\n".join(extracted_texts)

    async def should_process_article(self, session: aiohttp.ClientSession, new_item: Notice, old_item: Notice) -> bool:
        # 1. Metadata Check
        if new_item.title != old_item.title: return True
        if new_item.content != old_item.content: return True
        if len(new_item.attachments) != len(old_item.attachments): return True
        
        new_urls = {a.url for a in new_item.attachments}
        old_urls = {a.url for a in old_item.attachments}
        if new_urls != old_urls: return True

        # 2. HEAD Request Check
        old_att_map = {a.url: a for a in old_item.attachments}
        for new_att in new_item.attachments:
            old_att = old_att_map.get(new_att.url)
            if not old_att: return True

            meta = await self.fetcher.fetch_file_head(session, new_att.url, new_item.url)
            if meta["status"] != 200:
                logger.warning(f"[SMART-UPDATE] HEAD failed for {new_att.name}. Assuming changed.")
                return True
            
            remote_size = meta["content_length"]
            remote_etag = meta["etag"]

            if remote_etag:
                if not old_att.etag or remote_etag != old_att.etag:
                    return True
            elif remote_size > 0:
                if not old_att.file_size or remote_size != old_att.file_size:
                    return True
            else:
                return True # Missing metadata, force update

        return False

    async def detect_modifications(self, item: Notice, old_notice: Notice) -> Dict:
        changes = {}
        if old_notice.title != item.title:
            changes["title"] = f"'{old_notice.title}' -> '{item.title}'"

        if old_notice.content != item.content:
            if old_notice.content.strip() == item.content.strip():
                pass
            else:
                changes["old_content"] = old_notice.content
                changes["new_content"] = item.content
                diff_summary = await self.analyzer.get_diff_summary(old_notice.content, item.content)
                
                if diff_summary in ["NO_CHANGE", "변동사항 없음"] or "내용 변화는 없습니다" in diff_summary:
                    del changes["old_content"]
                    del changes["new_content"]
                else:
                    changes["content"] = diff_summary

        if (old_notice.attachment_text or "").strip() != (item.attachment_text or "").strip():
            changes["attachment_text"] = "첨부파일 내용 변경됨"

        # Image changes
        old_imgs = set(old_notice.image_urls) if old_notice.image_urls else set()
        new_imgs = set(item.image_urls) if item.image_urls else set()
        if old_imgs != new_imgs:
            changes["image"] = "이미지 변경됨"

        # Attachment changes (Granular)
        # Key: Name + Size (to detect content change even if name is same)
        # Note: ETag is not reliable/available as per analysis
        old_atts_map = {f"{a.name}_{a.file_size or 0}" : a.name for a in old_notice.attachments}
        new_atts_map = {f"{a.name}_{a.file_size or 0}" : a.name for a in item.attachments}
        
        old_keys = set(old_atts_map.keys())
        new_keys = set(new_atts_map.keys())
        
        added_keys = new_keys - old_keys
        removed_keys = old_keys - new_keys
        
        # Check for modifications (Same name, different size)
        # If name is in both added_keys (new size) and removed_keys (old size), it's a modification
        added_names = {new_atts_map[k] for k in added_keys}
        removed_names = {old_atts_map[k] for k in removed_keys}
        
        modified_names = added_names.intersection(removed_names)
        real_added = added_names - modified_names
        real_removed = removed_names - modified_names
        
        if modified_names:
            # We don't have a specific field for modified attachments in formatters yet,
            # but we can list them as Removed/Added or just add a note.
            # Plan said "Added/Removed". 
            # But "Modified" is better UX. 
            # Let's map them to "attachments_modified" if supported, or just list as Added/Removed.
            # Formatters support "attachments_added" and "attachments_removed".
            # Let's add "attachments_modified" to formatters later if needed, 
            # OR just put them in "attachments" generic field.
            # Actually, let's just treat them as Added/Removed for now to match Plan strictly,
            # OR better: Add them to "attachments_added" and "attachments_removed" so they show up.
            # Wait, if I put it in both, user sees "Added A, Removed A". That's explicit.
            pass

        if real_added or modified_names:
            changes["attachments_added"] = list(real_added | modified_names)
        if real_removed or modified_names:
            changes["attachments_removed"] = list(real_removed | modified_names)
            
        if added_keys or removed_keys:
             changes["attachments"] = "목록 변경됨" # Legacy flag

        return changes



    async def run_test(self, test_url: str):
        """
        Forces a notification for a specific URL.
        """
        from bs4 import BeautifulSoup
        logger.info(f"[TEST] Starting test run for: {test_url}")

        target = None
        for t in self.targets:
            if t["base_url"] in test_url or t["url"] in test_url:
                target = t
                break

        if not target:
            logger.warning(f"[TEST] No matching target found for {test_url}. Using generic parser.")
            target = self.targets[0]

        session = await self.fetcher.create_session()
        
        # Authenticate if needed for test URL
        if target and target["key"].startswith("eoullim_"):
            logger.info(f"[TEST] Eoullim target detected. Performing login...")
            cookies = await self.auth_service.get_eoullim_cookies()
            if cookies:
                self.fetcher.set_cookies(session, cookies)
                
        async with session:
            try:
                html = await self.fetcher.fetch_url(session, test_url)
                
                # Auto-detect list vs detail
                # Simple heuristic: if it has a list table, it's a list.
                # But test_url is usually a specific article.
                # We'll assume it's a detail page and try to parse it.
                # But we need a dummy 'item' first.
                
                dummy_item = Notice(
                    site_key=target["key"],
                    article_id="test",
                    title="Test Notice",
                    url=test_url,
                    content=""
                )
                
                item = self.parser.parse_detail(target["parser"], html, dummy_item)
                
                # Analyze
                item = await self.analyzer.analyze_notice(item)
                
                logger.info(f"[TEST] Parsed Item: {item.title}")
                logger.info(f"[TEST] Summary: {item.summary}")
                
                # Send Notification
                await self.notifier.send_telegram(session, item, is_new=True, modified_reason="[TEST RUN]")
                await self.notifier.send_discord(session, item, is_new=True, modified_reason="[TEST RUN]")
                
            except Exception as e:
                logger.error(f"[TEST] Failed: {e}")
