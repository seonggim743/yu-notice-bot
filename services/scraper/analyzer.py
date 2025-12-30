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
        """
        # Safely get key from injected AI service or config
        openai_api_key = getattr(self.ai, 'openai_api_key', None)
        if not openai_api_key and not self.no_ai_mode:
            # Fallback to env var if needed, but for now just log error
            pass

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
                         await self.ai.get_embedding(f"{notice.title}\n{notice.summary}") 
                     return notice
                 else:
                     # Just text but short -> Use as summary
                     notice.summary = notice.content.strip()[:200]
                     logger.info(f"[ANALYZER] Skipped AI summary for short text notice")
                     if not self.no_ai_mode:
                         await self.ai.get_embedding(f"{notice.title}\n{notice.summary}")
                     return notice

            logger.info(f"[ANALYZER] Waiting {self.AI_CALL_DELAY}s before analyze_notice...")
            await asyncio.sleep(self.AI_CALL_DELAY)

            # Define Category Sets based on site_key
            default_categories = ["학사", "장학", "행사", "채용", "일반", "비교과"]
            
            category_map = {
                "eoullim_career": ["특강", "교육", "상담", "캠프", "모의시험"],
                "eoullim_external": ["공모전", "대외활동", "봉사", "인턴", "채용", "교육"],
                "eoullim_study": ["어학", "자격증", "면접", "직무", "기타"],
            }
            
            # Select target categories
            target_categories = category_map.get(notice.site_key, default_categories)
            category_str = ", ".join(target_categories)

            system_prompt = (
                "You are a helpful assistant that summarizes university notices."
                "Summarize the content in 3 bullet points in Korean."
                f"Also classify the notice into ONE of these categories: [{category_str}]."
                "Respond in JSON format: {'summary': '...', 'category': '...'}"
            )

            user_content = f"Title: {notice.title}\n\nContent:\n{notice.content[:3000]}"
            if notice.attachment_text:
                user_content += f"\n\nAttachments:\n{notice.attachment_text[:1000]}"

            # Direct OpenAI API Call
            if not openai_api_key:
                 logger.error("[ANALYZER] OpenAI API key not configured for AI service.")
                 notice.category = "일반"
                 notice.summary = notice.content[:100] + " (AI 설정 오류)"
                 notice.embedding = []
                 return notice

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                }
                
                headers = {
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json",
                }

                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = json.loads(data["choices"][0]["message"]["content"])
                        
                        notice.summary = result.get("summary", "")
                        notice.category = result.get("category", "일반")
                        
                        # Validate category
                        if notice.category not in target_categories:
                             notice.category = target_categories[0] if target_categories else "일반"

                        logger.info(f"[ANALYZER] AI Analysis complete. Category: {notice.category}")
                    else:
                        logger.error(f"[ANALYZER] OpenAI API Error: {await resp.text()}")
                        notice.category = "일반"
                        notice.summary = notice.content[:100] + " (AI 오류)"

            # Generate Embedding using Supabase/OpenAI
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
            notice.embedding = [] # Ensure embedding is set even on error
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

