"""
ScraperService - Main orchestrator for notice scraping.
Refactored to use component-based architecture with dependency injection.
"""
import asyncio
import aiohttp
from typing import Dict, List, Optional

from core.config import settings
from core.logger import get_logger
from core.exceptions import NetworkException, ScraperException
from core.interfaces import INotificationService, IFileService, INoticeRepository
from core import constants
from core.performance import get_performance_monitor

from models.notice import Notice
from repositories.notice_repo import NoticeRepository
from services.notification_service import NotificationService
from services.file_service import FileService
from services.auth_service import AuthService

# Component imports
from services.components import (
    TargetManager,
    HashCalculator,
    ChangeDetector,
    AttachmentProcessor,
)

# Scraper sub-components
from services.scraper.fetcher import NoticeFetcher
from services.scraper.parser import NoticeParser
from services.scraper.analyzer import ContentAnalyzer

logger = get_logger(__name__)


class ScraperService:
    """
    Main scraper service that orchestrates notice fetching, parsing, and notification.
    
    Uses a component-based architecture for better separation of concerns:
    - TargetManager: Target loading and filtering
    - HashCalculator: Content hashing
    - ChangeDetector: Change detection and modification tracking
    - AttachmentProcessor: Attachment handling
    
    Supports dependency injection for easier testing and extensibility.
    """
    
    NOTICE_PROCESS_DELAY = constants.NOTICE_PROCESS_DELAY
    
    def __init__(
        self,
        init_mode: bool = False,
        no_ai_mode: bool = False,
        # Core dependencies (optional - defaults to real implementations)
        notifier: Optional[INotificationService] = None,
        file_service: Optional[IFileService] = None,
        repo: Optional[INoticeRepository] = None,
        # Component dependencies (optional)
        target_manager: Optional[TargetManager] = None,
        hash_calculator: Optional[HashCalculator] = None,
        change_detector: Optional[ChangeDetector] = None,
        attachment_processor: Optional[AttachmentProcessor] = None,
        # Internal components (optional)
        fetcher: Optional[NoticeFetcher] = None,
        parser: Optional[NoticeParser] = None,
        analyzer: Optional[ContentAnalyzer] = None,
        auth_service: Optional[AuthService] = None,
    ):
        """
        Initialize ScraperService with optional dependency injection.
        
        Args:
            init_mode: If True, seeds database without AI analysis or notifications
            no_ai_mode: If True, skips AI analysis
            notifier: Notification service for sending alerts
            file_service: File service for text extraction
            repo: Repository for database operations
            target_manager: Component for target management
            hash_calculator: Component for hash calculation
            change_detector: Component for change detection
            attachment_processor: Component for attachment processing
            fetcher: Network fetcher
            parser: Notice parser
            analyzer: Content analyzer (AI)
            auth_service: Authentication service
        """
        self.init_mode = init_mode
        self.no_ai_mode = no_ai_mode
        
        # Core dependencies
        self.notifier = notifier or NotificationService()
        self.file_service = file_service or FileService()
        self.repo = repo or NoticeRepository()
        
        # Internal components
        self.fetcher = fetcher or NoticeFetcher()
        self.parser = parser or NoticeParser()
        self.analyzer = analyzer or ContentAnalyzer(no_ai_mode=no_ai_mode)
        self.auth_service = auth_service or AuthService()
        
        # Extracted components (with proper dependency wiring)
        self.target_manager = target_manager or TargetManager()
        self.hash_calculator = hash_calculator or HashCalculator()
        self.change_detector = change_detector or ChangeDetector(
            fetcher=self.fetcher,
            ai_service=self.analyzer
        )
        self.attachment_processor = attachment_processor or AttachmentProcessor(
            file_service=self.file_service,
            fetcher=self.fetcher
        )
        
        # Load targets
        self.target_manager.load_targets()
    
    @property
    def targets(self) -> List[Dict]:
        """Returns the current list of targets."""
        return self.target_manager.get_targets()
    
    def filter_targets(self, target_key: str) -> None:
        """Filters targets to only include the specified key."""
        self.target_manager.filter_targets(target_key)
    
    def calculate_hash(self, notice: Notice) -> str:
        """Calculates content hash for a notice."""
        return self.hash_calculator.calculate_hash(notice)
    
    async def run(self) -> bool:
        """
        Runs the scraper for all loaded targets.
        
        Returns:
            True if all targets succeeded, False if any failed.
        """
        # Group targets by authentication type
        groups = self.target_manager.get_targets_by_auth_type()
        public_targets = groups["public"]
        eoullim_targets = groups["eoullim"]
        yutopia_targets = groups["yutopia"]
        
        success = True
        monitor = get_performance_monitor()
        
        session = await self.fetcher.create_session()
        
        async with session:
            with monitor.measure("full_scrape_run"):
                
                # 1. Public Targets (No Auth)
                if public_targets:
                    logger.info(f"[SCRAPER] Processing {len(public_targets)} public targets...")
                    for target in public_targets:
                        try:
                            await self.process_target(session, target)
                        except Exception as e:
                            logger.error(f"[SCRAPER] Public Target {target['key']} failed: {e}")
                            success = False
                            await self._send_error_alert(target, e)
                
                # 2. Eoullim Targets
                if eoullim_targets:
                    success = await self._process_eoullim_targets(
                        session, eoullim_targets, success
                    )
                
                # 3. YUtopia Targets
                if yutopia_targets:
                    success = await self._process_yutopia_targets(
                        session, yutopia_targets, success
                    )
            
            logger.info(f"[SCRAPER] Complete. Success: {success}")
            monitor.log_summary()
            return success
    
    async def _process_eoullim_targets(
        self,
        session: aiohttp.ClientSession,
        targets: List[Dict],
        current_success: bool
    ) -> bool:
        """Process Eoullim targets with authentication."""
        logger.info(f"[SCRAPER] Processing {len(targets)} Eoullim targets...")
        success = current_success
        
        try:
            cookies = await self.auth_service.get_eoullim_cookies()
            if cookies:
                session.cookie_jar.clear()
                self.fetcher.set_cookies(session, cookies)
                
                for target in targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] Eoullim Target {target['key']} failed: {e}")
                        success = False
                        await self._send_error_alert(target, e)
            else:
                logger.error("[SCRAPER] Eoullim Authentication failed. Skipping targets.")
                success = False
        except Exception as e:
            logger.error(f"[SCRAPER] Eoullim Auth Error: {e}")
            success = False
        
        return success
    
    async def _process_yutopia_targets(
        self,
        session: aiohttp.ClientSession,
        targets: List[Dict],
        current_success: bool
    ) -> bool:
        """Process YUtopia targets with authentication and session warmup."""
        logger.info(f"[SCRAPER] Processing {len(targets)} YUtopia targets...")
        success = current_success
        
        try:
            cookies = await self.auth_service.get_yutopia_cookies()
            if cookies:
                session.cookie_jar.clear()
                self.fetcher.set_cookies(session, cookies)
                
                # Session Warmup
                try:
                    warmup_url = constants.YUTOPIA_SESSION_WARMUP_URL
                    logger.info(f"[SCRAPER] Warming up YUtopia session: {warmup_url}")
                    async with session.get(warmup_url) as resp:
                        await resp.read()
                    logger.info("[SCRAPER] YUtopia session warmup complete.")
                except Exception as e:
                    logger.warning(f"[SCRAPER] YUtopia session warmup failed: {e}")
                
                for target in targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] YUtopia Target {target['key']} failed: {e}")
                        success = False
                        await self._send_error_alert(target, e)
            else:
                logger.error("[SCRAPER] YUtopia Authentication failed. Skipping targets.")
                success = False
        except Exception as e:
            logger.error(f"[SCRAPER] YUtopia Auth Error: {e}")
            success = False
        
        return success
    
    async def _send_error_alert(self, target: Dict, e: Exception) -> None:
        """Helper to send error alerts."""
        from core.error_notifier import get_error_notifier, ErrorSeverity
        await get_error_notifier().send_critical_error(
            f"Target '{target['key']}' failed during scrape",
            exception=e,
            context={"key": target["key"]},
            severity=ErrorSeverity.ERROR
        )
    
    async def process_target(self, session: aiohttp.ClientSession, target: Dict) -> None:
        """
        Processes a single scraping target.
        
        Args:
            session: aiohttp session
            target: Target configuration dictionary
        """
        key = target["key"]
        monitor = get_performance_monitor()
        
        with monitor.measure("scrape_target", {"key": key}):
            logger.info(f"[SCRAPER] Scraping {key}...")
        
        # Fetch list page
        try:
            html = await self.fetcher.fetch_url(session, target["url"])
        except NetworkException as e:
            e.details["key"] = key
            raise e
        
        # Parse notice list
        items = self.parser.parse_list(target["parser"], html, key, target["base_url"])
        processed_ids = self.repo.get_last_processed_ids(key, limit=1000)
        
        for item in items:
            await self._process_single_notice(session, target, item, processed_ids)
    
    async def _process_single_notice(
        self,
        session: aiohttp.ClientSession,
        target: Dict,
        item: Notice,
        processed_ids: Dict
    ) -> None:
        """
        Processes a single notice item.
        
        Args:
            session: aiohttp session
            target: Target configuration
            item: Notice item from list parsing
            processed_ids: Dict of previously processed article IDs to hashes
        """
        key = target["key"]
        is_new = item.article_id not in processed_ids
        old_hash = processed_ids.get(item.article_id)
        old_notice = None
        
        # Fetch detail page
        try:
            detail_html = await self.fetcher.fetch_url(session, item.url)
        except Exception as e:
            logger.warning(f"[SCRAPER] Failed to fetch detail for {item.title}: {e}")
            return
        
        # Parse detail
        item = self.parser.parse_detail(target["parser"], detail_html, item)
        
        # Validate content
        has_media = bool(item.attachments or item.image_urls)
        if (not item.content or len(item.content.strip()) < 10) and not has_media:
            logger.warning(
                f"[SCRAPER] Empty or very short content for '{item.title}' and no media. Skipping."
            )
            return
        
        # Smart Update Check for existing notices
        if not is_new:
            old_notice = self.repo.get_notice(key, item.article_id)
            if old_notice:
                should_process = await self.change_detector.should_process_article(
                    session, item, old_notice
                )
                if not should_process:
                    logger.info(f"[SCRAPER] No changes detected for '{item.title}'. Skipping.")
                    return
                logger.info(f"[SCRAPER] Changes detected for '{item.title}'. Reprocessing.")
        
        # Process Attachments
        if item.attachments:
            await self.attachment_processor.process_attachments(session, item)
        
        # Calculate Hash
        current_hash = self.calculate_hash(item)
        item.content_hash = current_hash
        
        # Check for modifications
        is_modified = False
        modified_reason = ""
        
        if not is_new:
            if old_hash == current_hash:
                return  # No changes
            is_modified = True
            modified_reason = "내용 또는 제목 변경됨"
        
        logger.info(f"Processing {'New' if is_new else 'Modified'}: {item.title}")
        
        # Init Mode - Skip AI and notifications
        if self.init_mode:
            logger.info(f"[INIT] Seeding database with: {item.title}")
            item.category = "일반"
            item.summary = "초기화 모드로 저장됨 (AI 요약 없음)"
            item.embedding = None
            self.repo.upsert_notice(item)
            return
        
        # AI Analysis
        item = await self._analyze_notice(item, key, old_notice)
        
        # Detect modifications for existing notices
        changes = None
        if is_modified and old_notice:
            changes = await self.change_detector.detect_modifications(item, old_notice)
            
            if not changes:
                logger.info(
                    f"[SCRAPER] Hash mismatch but no content changes detected for "
                    f"'{item.title}'. Updating hash only."
                )
                self.repo.upsert_notice(item)
                return
            
            item.change_details = changes
            modified_reason = self._build_modified_reason(changes)
        
        # Save to DB
        notice_id = self.repo.upsert_notice(item)
        
        if notice_id:
            await self._send_notifications(
                session, item, is_new, modified_reason, old_notice, changes
            )
        
        await asyncio.sleep(self.NOTICE_PROCESS_DELAY)
    
    async def _analyze_notice(
        self,
        item: Notice,
        key: str,
        old_notice: Optional[Notice]
    ) -> Notice:
        """Analyzes notice with AI, reusing old results if content unchanged."""
        
        # Special case for menu
        if key == "dormitory_menu":
            item.category = "식단"
            item.tags = ["기숙사"]
            item.summary = "기숙사 식단표입니다."
            return item
        
        # Try to reuse AI results if content identical
        if old_notice:
            if (old_notice.content or "").strip() == (item.content or "").strip():
                logger.info(
                    f"[SCRAPER] Content identical to previous version. "
                    f"Reusing AI metadata for '{item.title}'."
                )
                item.summary = old_notice.summary
                item.category = old_notice.category
                item.tags = old_notice.tags
                item.embedding = old_notice.embedding
                return item
        
        # Run AI analysis
        item = await self.analyzer.analyze_notice(item)
        
        # Force dormitory tag for dormitory_notice
        if key == "dormitory_notice":
            if "기숙사" not in item.tags:
                item.tags.insert(0, "기숙사")
        
        return item
    
    def _build_modified_reason(self, changes: Dict) -> str:
        """Builds human-readable modification reason from changes dict."""
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
        return ", ".join(reasons) if reasons else "내용 변경됨"
    
    async def _send_notifications(
        self,
        session: aiohttp.ClientSession,
        item: Notice,
        is_new: bool,
        modified_reason: str,
        old_notice: Optional[Notice],
        changes: Optional[Dict]
    ) -> None:
        """Sends notifications via Telegram and Discord."""
        notice_id = self.repo.get_notice_id(item.site_key, item.article_id)
        
        # Telegram
        existing_message_id = None
        if not is_new and old_notice:
            existing_message_id = old_notice.message_ids.get("telegram") if old_notice.message_ids else None
        
        msg_id = await self.notifier.send_telegram(
            session, item, is_new, modified_reason,
            existing_message_id=existing_message_id,
            changes=changes
        )
        if msg_id and notice_id:
            self.repo.update_message_ids(notice_id, "telegram", msg_id)
        
        # Discord
        existing_thread_id = None
        if not is_new and old_notice:
            existing_thread_id = old_notice.discord_thread_id
        
        discord_thread_id = await self.notifier.send_discord(
            session, item, is_new, modified_reason,
            existing_thread_id=existing_thread_id,
            changes=changes
        )
        if discord_thread_id and notice_id:
            self.repo.update_discord_thread_id(notice_id, discord_thread_id)
    
    async def run_test(self, test_url: str) -> None:
        """
        Forces a notification for a specific URL.
        Useful for testing the full pipeline.
        
        Args:
            test_url: URL to test
        """
        logger.info(f"[TEST] Starting test run for: {test_url}")
        
        # Find matching target
        target = None
        for t in self.targets:
            if t["base_url"] in test_url or t["url"] in test_url:
                target = t
                break
        
        if not target:
            logger.warning(f"[TEST] No matching target found for {test_url}. Using first target.")
            target = self.targets[0] if self.targets else None
        
        if not target:
            logger.error("[TEST] No targets available")
            return
        
        session = await self.fetcher.create_session()
        
        # Authenticate if needed
        if target["key"].startswith("eoullim_"):
            logger.info("[TEST] Eoullim target detected. Performing login...")
            cookies = await self.auth_service.get_eoullim_cookies()
            if cookies:
                self.fetcher.set_cookies(session, cookies)
        elif target["key"] == "yutopia":
            logger.info("[TEST] YUtopia target detected. Performing login...")
            cookies = await self.auth_service.get_yutopia_cookies()
            if cookies:
                self.fetcher.set_cookies(session, cookies)
            
            # Session warmup
            try:
                warmup_url = constants.YUTOPIA_SESSION_WARMUP_URL
                logger.info(f"[TEST] Warming up YUtopia session: {warmup_url}")
                async with session.get(warmup_url) as resp:
                    await resp.read()
                logger.info("[TEST] YUtopia session warmup complete.")
            except Exception as e:
                logger.warning(f"[TEST] YUtopia session warmup failed: {e}")
        
        async with session:
            try:
                html = await self.fetcher.fetch_url(session, test_url)
                
                # Create dummy item for parsing
                dummy_item = Notice(
                    site_key=target["key"],
                    article_id="test",
                    title="Test Notice",
                    url=test_url,
                    content=""
                )
                
                item = self.parser.parse_detail(target["parser"], html, dummy_item)
                item = await self.analyzer.analyze_notice(item)
                
                logger.info(f"[TEST] Parsed Item: {item.title}")
                logger.info(f"[TEST] Summary: {item.summary}")
                
                # Process attachments
                if item.attachments:
                    logger.info(f"[TEST] Processing {len(item.attachments)} attachments...")
                    await self.attachment_processor.process_attachments(session, item)
                
                # Send notifications
                await self.notifier.send_telegram(
                    session, item, is_new=True, modified_reason="[TEST RUN]"
                )
                await self.notifier.send_discord(
                    session, item, is_new=True, modified_reason="[TEST RUN]"
                )
                
            except Exception as e:
                logger.error(f"[TEST] Failed: {e}")
