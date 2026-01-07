"""
ChangeDetector component for detecting notice modifications.
Extracted from ScraperService for Single Responsibility Principle.
"""
import aiohttp
from typing import Dict, Optional

from core.logger import get_logger
from core.interfaces import IAIService
from models.notice import Notice
from services.scraper.fetcher import NoticeFetcher

logger = get_logger(__name__)


class ChangeDetector:
    """
    Detects changes between old and new versions of notices.
    Provides granular change detection for titles, content, attachments, and images.
    """
    
    def __init__(
        self,
        fetcher: Optional[NoticeFetcher] = None,
        ai_service: Optional[IAIService] = None,
    ):
        """
        Initialize ChangeDetector.
        
        Args:
            fetcher: NoticeFetcher for making HEAD requests to check attachments
            ai_service: AI service for generating diff summaries (optional)
        """
        self.fetcher = fetcher or NoticeFetcher()
        self.ai_service = ai_service
    
    async def should_process_article(
        self,
        session: aiohttp.ClientSession,
        new_item: Notice,
        old_item: Notice
    ) -> bool:
        """
        Determines if an article should be processed (has changes).
        
        Uses a multi-stage check:
        1. Metadata comparison (title, content, attachment count)
        2. HEAD requests for attachment changes (size/ETag)
        
        Args:
            session: aiohttp session for HEAD requests
            new_item: Newly scraped notice
            old_item: Previously stored notice
            
        Returns:
            True if the article has changes and should be processed
        """
        # 1. Quick Metadata Check
        if new_item.title != old_item.title:
            logger.debug(f"[CHANGE_DETECTOR] Title changed for {new_item.article_id}")
            return True
            
        if new_item.content != old_item.content:
            logger.debug(f"[CHANGE_DETECTOR] Content changed for {new_item.article_id}")
            return True
            
        if len(new_item.attachments) != len(old_item.attachments):
            logger.debug(f"[CHANGE_DETECTOR] Attachment count changed for {new_item.article_id}")
            return True
        
        # 2. Image Changes
        old_imgs = set(old_item.image_urls) if old_item.image_urls else set()
        new_imgs = set(new_item.image_urls) if new_item.image_urls else set()
        if old_imgs != new_imgs:
            logger.debug(f"[CHANGE_DETECTOR] Images changed for {new_item.article_id}")
            return True
        
        # 3. Deep Attachment Check (HEAD requests)
        for i, new_att in enumerate(new_item.attachments):
            if i >= len(old_item.attachments):
                return True  # New attachment added
                
            old_att = old_item.attachments[i]
            
            # Name mismatch
            if new_att.name != old_att.name:
                logger.debug(f"[CHANGE_DETECTOR] Attachment name changed: {old_att.name} -> {new_att.name}")
                return True
            
            # URL mismatch (rare but possible)
            if new_att.url != old_att.url:
                logger.debug(f"[CHANGE_DETECTOR] Attachment URL changed for {new_att.name}")
                return True
            
            # Size/ETag check via HEAD request
            if old_att.file_size or old_att.etag:
                try:
                    meta = await self.fetcher.fetch_file_head(session, new_att.url, new_item.url)
                    
                    # Check if HEAD request returned valid data
                    if not meta or (not meta.get("content_length") and not meta.get("etag")):
                        # HEAD failed or returned no useful data, force update
                        logger.debug(f"[CHANGE_DETECTOR] HEAD request returned no data for {new_att.name}, forcing update")
                        return True
                    
                    if meta.get("content_length") and old_att.file_size:
                        if meta["content_length"] != old_att.file_size:
                            logger.debug(
                                f"[CHANGE_DETECTOR] Attachment size changed for {new_att.name}: "
                                f"{old_att.file_size} -> {meta['content_length']}"
                            )
                            return True
                            
                    if meta.get("etag") and old_att.etag:
                        if meta["etag"] != old_att.etag:
                            logger.debug(f"[CHANGE_DETECTOR] Attachment ETag changed for {new_att.name}")
                            return True
                except Exception as e:
                    # HEAD request failed, force update to be safe
                    logger.debug(f"[CHANGE_DETECTOR] HEAD request exception for {new_att.name}: {e}, forcing update")
                    return True
            else:
                # No metadata stored, force update
                return True
        
        return False
    
    async def detect_modifications(
        self,
        new_item: Notice,
        old_notice: Notice
    ) -> Dict:
        """
        Detects specific modifications between old and new notice versions.
        
        Returns a dictionary describing what changed:
        - title: Title change description
        - content: AI-generated diff summary
        - old_content/new_content: Raw content for detailed diff display
        - attachment_text: Flag if attachment text changed
        - image: Flag if images changed
        - attachments_added: List of added attachment names
        - attachments_removed: List of removed attachment names
        - attachments: Legacy flag for any attachment change
        
        Args:
            new_item: Newly scraped notice
            old_notice: Previously stored notice
            
        Returns:
            Dictionary of detected changes
        """
        changes = {}
        
        # Title changes
        if old_notice.title != new_item.title:
            changes["title"] = f"'{old_notice.title}' -> '{new_item.title}'"
        
        # Content changes
        if old_notice.content != new_item.content:
            # Check for whitespace-only changes
            if old_notice.content.strip() != new_item.content.strip():
                changes["old_content"] = old_notice.content
                changes["new_content"] = new_item.content
                
                # Get AI diff summary if service available
                if self.ai_service:
                    try:
                        diff_summary = await self.ai_service.get_diff_summary(
                            old_notice.content,
                            new_item.content
                        )
                        
                        # Filter out "no change" responses
                        if diff_summary and diff_summary not in ["NO_CHANGE", "변동사항 없음"]:
                            if "내용 변화는 없습니다" not in diff_summary:
                                changes["content"] = diff_summary
                            else:
                                # AI says no meaningful change, remove raw content
                                del changes["old_content"]
                                del changes["new_content"]
                        else:
                            del changes["old_content"]
                            del changes["new_content"]
                    except Exception as e:
                        logger.warning(f"[CHANGE_DETECTOR] AI diff summary failed: {e}")
                        changes["content"] = "내용 변경됨"
                else:
                    changes["content"] = "내용 변경됨"
        
        # Attachment text changes
        old_att_text = (old_notice.attachment_text or "").strip()
        new_att_text = (new_item.attachment_text or "").strip()
        if old_att_text != new_att_text:
            changes["attachment_text"] = "첨부파일 내용 변경됨"
        
        # Image changes
        old_imgs = set(old_notice.image_urls) if old_notice.image_urls else set()
        new_imgs = set(new_item.image_urls) if new_item.image_urls else set()
        if old_imgs != new_imgs:
            changes["image"] = "이미지 변경됨"
        
        # Attachment changes (granular)
        changes.update(self._detect_attachment_changes(old_notice, new_item))
        
        return changes
    
    def _detect_attachment_changes(self, old_notice: Notice, new_item: Notice) -> Dict:
        """
        Detects granular attachment changes.
        
        Args:
            old_notice: Previously stored notice
            new_item: Newly scraped notice
            
        Returns:
            Dictionary with attachments_added, attachments_removed, attachments keys
        """
        changes = {}
        
        # Create maps using name + size as key
        old_atts_map = {
            f"{a.name}_{a.file_size or 0}": a.name
            for a in old_notice.attachments
        }
        new_atts_map = {
            f"{a.name}_{a.file_size or 0}": a.name
            for a in new_item.attachments
        }
        
        old_keys = set(old_atts_map.keys())
        new_keys = set(new_atts_map.keys())
        
        added_keys = new_keys - old_keys
        removed_keys = old_keys - new_keys
        
        # Detect modifications (same name, different size)
        added_names = {new_atts_map[k] for k in added_keys}
        removed_names = {old_atts_map[k] for k in removed_keys}
        
        modified_names = added_names.intersection(removed_names)
        real_added = added_names - modified_names
        real_removed = removed_names - modified_names
        
        # Combine real additions with modifications
        if real_added or modified_names:
            changes["attachments_added"] = list(real_added | modified_names)
            
        if real_removed or modified_names:
            changes["attachments_removed"] = list(real_removed | modified_names)
        
        # Legacy flag
        if added_keys or removed_keys:
            changes["attachments"] = "목록 변경됨"
        
        return changes
