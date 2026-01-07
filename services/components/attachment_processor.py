"""
AttachmentProcessor component for handling notice attachments.
Extracted from ScraperService for Single Responsibility Principle.
"""
import asyncio
import aiohttp
from typing import List, Optional, Tuple

from core.logger import get_logger
from core.interfaces import IFileService
from core import constants
from models.notice import Notice, Attachment
from services.scraper.fetcher import NoticeFetcher

logger = get_logger(__name__)


class AttachmentProcessor:
    """
    Processes notice attachments: downloads, extracts text, and generates previews.
    Handles concurrency limiting and error handling for attachment processing.
    """
    
    # File extensions that need processing (text extraction, preview generation)
    PROCESSABLE_EXTENSIONS = {"hwp", "hwpx", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
    
    # Extensions for text extraction
    TEXT_EXTRACTION_EXTENSIONS = {"hwp", "hwpx", "pdf"}
    
    # Maximum concurrent attachment processing
    MAX_CONCURRENCY = 2
    
    # Maximum attachments to process per notice
    MAX_ATTACHMENTS = 10
    
    def __init__(
        self,
        file_service: Optional[IFileService] = None,
        fetcher: Optional[NoticeFetcher] = None,
        max_previews: int = constants.MAX_PREVIEWS,
    ):
        """
        Initialize AttachmentProcessor.
        
        Args:
            file_service: File service for text extraction and preview generation
            fetcher: NoticeFetcher for downloading attachments
            max_previews: Maximum number of preview images to generate per notice
        """
        self.file_service = file_service
        self.fetcher = fetcher or NoticeFetcher()
        self.max_previews = max_previews
    
    async def process_attachments(
        self,
        session: aiohttp.ClientSession,
        notice: Notice
    ) -> None:
        """
        Processes all attachments for a notice.
        
        Downloads attachments, extracts text content, and generates preview images.
        Results are written back to the notice object.
        
        Args:
            session: aiohttp session for downloading
            notice: Notice object to process (modified in place)
        """
        if not notice.attachments:
            return
        
        if not self.file_service:
            logger.warning("[ATTACHMENT_PROCESSOR] No file service configured, skipping processing")
            return
        
        extracted_texts: List[str] = []
        
        # Limit concurrency to prevent CPU spike (Playwright/PDF processing is heavy)
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        
        # Create tasks for parallel processing
        tasks = [
            self._process_single_attachment(session, att, notice.url, semaphore)
            for att in notice.attachments[:self.MAX_ATTACHMENTS]
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Apply results
        preview_count = 0
        
        for i, (text_result, preview_result) in enumerate(results):
            if text_result:
                extracted_texts.append(text_result)
            
            att = notice.attachments[i]
            if preview_result and preview_count < self.max_previews:
                att.preview_images = preview_result
                preview_count += 1
        
        # Set extracted text on notice
        if extracted_texts:
            notice.attachment_text = "\n\n".join(extracted_texts)
    
    async def _process_single_attachment(
        self,
        session: aiohttp.ClientSession,
        att: Attachment,
        notice_url: str,
        semaphore: asyncio.Semaphore,
    ) -> Tuple[Optional[str], Optional[List[bytes]]]:
        """
        Processes a single attachment.
        
        Args:
            session: aiohttp session
            att: Attachment to process
            notice_url: URL of parent notice (for referer header)
            semaphore: Concurrency limiter
            
        Returns:
            Tuple of (extracted_text, preview_images)
        """
        async with semaphore:
            try:
                ext = self._get_extension(att.name)
                needs_processing = ext in self.PROCESSABLE_EXTENSIONS
                
                file_data = None
                
                if needs_processing:
                    logger.info(f"[ATTACHMENT_PROCESSOR] Downloading: {att.name}")
                    file_data = await self.fetcher.download_file(session, att.url, notice_url)
                    
                    if file_data:
                        att.file_size = len(file_data)
                else:
                    # Just get metadata via HEAD request
                    meta = await self.fetcher.fetch_file_head(session, att.url, notice_url)
                    att.file_size = meta.get("content_length", 0)
                    att.etag = meta.get("etag")
                
                if file_data:
                    text_result = self._extract_text(file_data, att.name, ext)
                    preview_result = self._generate_preview(file_data, att.name)
                    return text_result, preview_result
                
            except Exception as e:
                logger.warning(f"[ATTACHMENT_PROCESSOR] Failed to process {att.name}: {e}")
            
            return None, None
    
    def _extract_text(
        self,
        file_data: bytes,
        filename: str,
        ext: str
    ) -> Optional[str]:
        """
        Extracts text from attachment file.
        
        Args:
            file_data: Raw file bytes
            filename: Original filename
            ext: File extension
            
        Returns:
            Extracted text or None
        """
        if ext not in self.TEXT_EXTRACTION_EXTENSIONS:
            return None
        
        if not self.file_service:
            return None
        
        try:
            text = self.file_service.extract_text(file_data, filename)
            if text and len(text.strip()) > 100:
                return f"--- 첨부파일: {filename} ---\n{text.strip()[:3000]}..."
        except Exception as e:
            logger.warning(f"[ATTACHMENT_PROCESSOR] Text extraction failed for {filename}: {e}")
        
        return None
    
    def _generate_preview(
        self,
        file_data: bytes,
        filename: str,
        max_pages: int = 20
    ) -> Optional[List[bytes]]:
        """
        Generates preview images for attachment.
        
        Args:
            file_data: Raw file bytes
            filename: Original filename
            max_pages: Maximum pages to convert to images
            
        Returns:
            List of preview image bytes or None
        """
        if not self.file_service:
            return None
        
        try:
            preview_images = self.file_service.generate_preview_images(
                file_data,
                filename,
                max_pages=max_pages
            )
            if preview_images:
                return preview_images
        except Exception as e:
            logger.warning(f"[ATTACHMENT_PROCESSOR] Preview generation failed for {filename}: {e}")
        
        return None
    
    @staticmethod
    def _get_extension(filename: str) -> str:
        """Extracts lowercase extension from filename."""
        if "." in filename:
            return filename.split(".")[-1].lower()
        return ""
