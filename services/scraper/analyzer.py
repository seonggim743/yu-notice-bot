import asyncio
import aiohttp
import json
from typing import Optional, Dict
from models.notice import Notice
from services.ai_service import AIService
from core.logger import get_logger
from core.interfaces import IAIService
from core import constants

logger = get_logger(__name__)


class ContentAnalyzer:
    """
    Handles AI analysis and Diff generation.
    Manages rate limiting and error handling for AI calls.
    Supports dependency injection for testing.
    """

    def __init__(
        self,
        no_ai_mode: bool = False,
        ai_service: Optional[IAIService] = None,
    ):
        # Inject or create default instance
        self.ai = ai_service or AIService()
        self.no_ai_mode = no_ai_mode
        self.ai_summary_count = 0
        self.MAX_AI_SUMMARIES = constants.MAX_AI_SUMMARIES
        self.AI_CALL_DELAY = constants.AI_CALL_DELAY

    async def analyze_notice(self, notice: Notice) -> Notice:
        """
        Analyzes the notice content using LLM to generate a summary and category.
        Delegates to the injected AIService (supporting Gemini).
        """
        try:
            if self.no_ai_mode:
                notice.category = "일반"
                notice.summary = "AI 분석 건너뜀 (No-AI Mode)"
                notice.embedding = None
                return notice

            if self.ai_summary_count >= self.MAX_AI_SUMMARIES:
                logger.warning("[ANALYZER] AI limit reached. Skipping AI analysis.")
                notice.category = "일반"
                notice.summary = notice.content[:100] + " (AI 한도 도달)"
                notice.embedding = []
                return notice

            # Handle Short Content / Image Only
            content_len = len(notice.content.strip())
            att_text_len = len((notice.attachment_text or "").strip())
            has_media = bool(notice.image_urls or notice.attachments)
            
            if content_len < constants.SHORT_NOTICE_CONTENT_LENGTH and att_text_len < constants.SHORT_NOTICE_ATTACHMENT_LENGTH:
                 if has_media:
                     notice.summary = "이미지 또는 첨부파일을 확인해주세요."
                     logger.info(f"[ANALYZER] Skipped AI summary for Image/Attachment-only notice")
                     # Still get embedding for search
                     if not self.no_ai_mode:
                         notice.embedding = await self.ai.get_embedding(f"{notice.title}\n{notice.summary}") 
                     return notice
                 else:
                     # Just text but short -> Use as summary
                     notice.summary = notice.content.strip()[:200]
                     logger.info(f"[ANALYZER] Skipped AI summary for short text notice")
                     if not self.no_ai_mode:
                         notice.embedding = await self.ai.get_embedding(f"{notice.title}\n{notice.summary}")
                     return notice

            logger.info(f"[ANALYZER] Waiting {self.AI_CALL_DELAY}s before analyze_notice...")
            await asyncio.sleep(self.AI_CALL_DELAY)

            # Delegate to AIService
            # We pass the full content including attachment text if available
            full_content = notice.content
            if notice.attachment_text:
                full_content += f"\n\n[첨부파일 내용]\n{notice.attachment_text}"

            result = await self.ai.analyze_notice(
                text=full_content,
                site_key=notice.site_key,
                title=notice.title,
                author=notice.author or ""
            )

            notice.summary = result.get("summary", notice.content[:100] + " (요약 실패)")
            notice.category = result.get("category", "일반")
            notice.tags = result.get("tags", [])
            
            # Default fallback if category is missing/invalid managed by AIService prompt mostly, 
            # but we can enforce defaults if needed.
            if not notice.category:
                notice.category = "일반"

            logger.info(f"[ANALYZER] AI Analysis complete. Category: {notice.category}")

            # Generate Embedding
            logger.info(f"[ANALYZER] Waiting {self.AI_CALL_DELAY}s before get_embedding...")
            await asyncio.sleep(self.AI_CALL_DELAY)
            
            try:
                notice.embedding = await self.ai.get_embedding(f"{notice.title}\n{notice.summary}")
            except Exception as e:
                logger.error(f"[ANALYZER] Embedding failed: {e}")
                notice.embedding = []

            self.ai_summary_count += 1
            logger.info(f"[ANALYZER] AI complete. Quota: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}")
            return notice

        except Exception as e:
            logger.error(f"[ANALYZER] Analysis failed: {e}")
            notice.category = "일반"
            notice.summary = notice.content[:100] + " (AI 오류)"
            notice.embedding = [] 
            return notice

    async def get_diff_summary(self, old_content: str, new_content: str) -> str:
        """
        Generates a summary of changes between old and new content.
        """
        if self.ai_summary_count >= self.MAX_AI_SUMMARIES:
             return "내용 변경됨 (AI 한도 초과)"

        logger.info(f"[ANALYZER] Waiting {self.AI_CALL_DELAY}s before get_diff_summary...")
        await asyncio.sleep(self.AI_CALL_DELAY)
        
        try:
            diff = await self.ai.get_diff_summary(old_content, new_content)
            self.ai_summary_count += 1
            return diff
        except Exception as e:
            logger.error(f"[ANALYZER] Diff summary failed: {e}")
            return "내용 변경됨 (AI 오류)"

    async def extract_menu(self, image_url: str, image_data: bytes = None) -> Optional[Dict]:
        """
        Extracts menu data from an image URL with exponential backoff retry.
        """
        max_retries = 3
        attempt = 0
        
        while attempt < max_retries:
            try:
                return await self.ai.extract_menu_from_image(image_url, image_data)
            except Exception as e:
                attempt += 1
                error_msg = str(e)
                
                # Fail fast on client errors (4xx) if possible to detect
                # Assuming standard HTTP exceptions, but catching generic Exception for safety
                if "400" in error_msg or "404" in error_msg or "Bad Request" in error_msg:
                    logger.error(f"[ANALYZER] Menu extraction failed (Client Error): {e}")
                    return None

                if attempt >= max_retries:
                    logger.error(f"[ANALYZER] Menu extraction failed after {max_retries} retries: {e}")
                    return None
                
                # Exponential Backoff: 1s -> 2s -> 4s
                wait_time = 2 ** (attempt - 1)
                logger.warning(f"[ANALYZER] Menu extraction error (Attempt {attempt}/{max_retries}). Retrying in {wait_time}s... Error: {e}")
                await asyncio.sleep(wait_time)
        
        return None

