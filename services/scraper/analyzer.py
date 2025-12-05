import asyncio
from typing import Optional, Dict
from models.notice import Notice
from services.ai_service import AIService
from core.logger import get_logger
from core import constants

logger = get_logger(__name__)

class ContentAnalyzer:
    """
    Handles AI analysis and Diff generation.
    Manages rate limiting and error handling for AI calls.
    """
    def __init__(self, no_ai_mode: bool = False):
        self.ai = AIService()
        self.no_ai_mode = no_ai_mode
        self.ai_summary_count = 0
        self.MAX_AI_SUMMARIES = constants.MAX_AI_SUMMARIES
        self.AI_CALL_DELAY = constants.AI_CALL_DELAY

    async def analyze_notice(self, notice: Notice) -> Notice:
        """
        Performs AI analysis on the notice to extract metadata and summary.
        """
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

        # 1. Analyze Content
        logger.info(f"[ANALYZER] Waiting {self.AI_CALL_DELAY}s before analyze_notice...")
        await asyncio.sleep(self.AI_CALL_DELAY)

        full_text = f"{notice.content}\n\n{notice.attachment_text or ''}"
        
        try:
            analysis = await self.ai.analyze_notice(
                full_text,
                site_key=notice.site_key,
                title=notice.title,
                author=notice.author or "",
            )
            
            notice.category = analysis.get("category", "일반")
            notice.tags = analysis.get("tags", [])
            notice.deadline = analysis.get("deadline")
            notice.eligibility = analysis.get("eligibility", [])
            notice.start_date = analysis.get("start_date")
            notice.end_date = analysis.get("end_date")
            notice.target_grades = analysis.get("target_grades", [])
            notice.target_dept = analysis.get("target_dept")

            # Handle Short Content (Short Article / 단신)
            content_len = len(notice.content.strip())
            att_text_len = len((notice.attachment_text or "").strip())
            
            if content_len < constants.SHORT_NOTICE_CONTENT_LENGTH and att_text_len < constants.SHORT_NOTICE_ATTACHMENT_LENGTH:
                 notice.summary = f"[단신] {notice.content.strip()}"
                 logger.info(f"[ANALYZER] Treated as Short Article (단신)")
            else:
                 notice.summary = analysis.get("summary", notice.content[:100])

        except Exception as e:
            logger.error(f"[ANALYZER] AI Analysis failed: {e}")
            notice.category = "일반"
            notice.summary = notice.content[:100] + " (AI 오류)"

        # 2. Get Embedding
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

    async def extract_menu(self, image_url: str) -> Dict:
        """
        Extracts menu data from an image URL.
        """
        return await self.ai.extract_menu_from_image(image_url)

