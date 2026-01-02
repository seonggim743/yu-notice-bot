import google.generativeai as genai
import os
from typing import Dict, Any
import json
import asyncio
import random
import re
from core.config import settings
from core.logger import get_logger
from core.database import Database
from core import constants
from datetime import datetime, timedelta
import pytz

logger = get_logger(__name__)


def get_kst_reset_time() -> str:
    """
    Calculates the next Google API Reset Time (Midnight PT = 5 PM KST).
    Returns as ISO format string.
    """
    tz = pytz.timezone("Asia/Seoul")
    now = datetime.now(tz)
    # Today 5:01 PM
    reset_time = now.replace(hour=17, minute=1, second=0, microsecond=0)
    
    # If already past 5 PM, reset is tomorrow
    if now >= reset_time:
        reset_time += timedelta(days=1)
    
    return reset_time.isoformat()


def parse_error_type(error_msg: str) -> str:
    """
    Determines if the 429 error is RPD (Daily Limit) or RPM (Short-term).
    """
    error_msg = error_msg.lower()
    
    # RPD Indicators
    if "quota_metric" in error_msg and "requests_per_day" in error_msg:
        return "RPD"
    if "quota exceeded" in error_msg and ("day" in error_msg or "daily" in error_msg or "limit: 20" in error_msg):
        # limit: 20 is usually the daily limit for Pro/Flash experimental
        return "RPD"
    
    # RPM Indicators
    if "requests_per_minute" in error_msg or "rate limit" in error_msg:
        return "RPM"
        
    return "UNKNOWN"  # Default to short-term block if unsure


class AIService:
    def __init__(self):
        self.db = Database.get_client()
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)
        else:
            logger.warning("[AI] Gemini API Key missing. AI features disabled.")

        self.system_prompt_template = self._load_system_prompt()

    async def _get_available_models(self) -> list:
        """
        Fetches available models from DB, sorted by priority.
        Checks for blocked_until < NOW() or NULL.
        """
        if not self.db:
            return [settings.GEMINI_MODEL]

        try:
            # Supabase/PostgREST query
            now_iso = datetime.now(pytz.utc).isoformat()
            
            # Logic: (blocked_until IS NULL) OR (blocked_until < NOW)
            # PostgREST complex filter is tricky, so we fetch all active models and filter in Python
            # This is safer for small tables (7 rows).
            response = self.db.table("ai_models") \
                .select("*") \
                .eq("is_active", True) \
                .order("priority", desc=False) \
                .execute()
                
            models = response.data
            valid_models = []
            
            for m in models:
                blocked_until = m.get("blocked_until")
                if not blocked_until:
                    valid_models.append(m["model_name"])
                    continue
                
                # Check expiry
                # Supabase returns ISO string with TZ
                try:
                    blocked_dt = datetime.fromisoformat(blocked_until.replace('Z', '+00:00'))
                    if datetime.now(blocked_dt.tzinfo) > blocked_dt:
                         # Unblock (Optional: Clean up DB, but lazy check is fine)
                        valid_models.append(m["model_name"])
                except Exception:
                    # Parse error, assume valid to be safe? Or invalid?
                    # Assume valid and let it fail if needed.
                    valid_models.append(m["model_name"])

            if not valid_models:
                logger.warning("[AI] All models are currently blocked in DB.")
                return []
                
            return valid_models

        except Exception as e:
            logger.error(f"[AI] Failed to fetch models from DB: {e}")
            # Fallback to config default
            return [settings.GEMINI_MODEL]

    async def _block_model(self, model_name: str, reason: str):
        """
        Updates the blocked_until timestamp in DB.
        """
        if not self.db:
            return

        try:
            if reason == "RPD":
                until = get_kst_reset_time()
                logger.warning(f"[AI] Blocking {model_name} until {until} (Daily Limit)")
            else:
                # RPM: Block for 2 minutes
                until = (datetime.now(pytz.utc) + timedelta(minutes=2)).isoformat()
                logger.warning(f"[AI] Blocking {model_name} until {until} (Rate Limit)")

            self.db.table("ai_models") \
                .update({"blocked_until": until}) \
                .eq("model_name", model_name) \
                .execute()
        except Exception as e:
            logger.error(f"[AI] Failed to block model {model_name}: {e}")

    def _load_system_prompt(self) -> str:
        """Loads the system prompt from resources/prompts/system_prompt.txt"""
        try:
            prompt_path = os.path.join(
                os.path.dirname(__file__), "../resources/prompts/system_prompt.txt"
            )
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"[AI] Failed to load system prompt: {e}")
            return ""

    async def _save_token_usage(self, prompt_tokens: int, completion_tokens: int):
        if not self.db:
            return
        try:
            data = {
                "model": settings.GEMINI_MODEL,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            self.db.table("token_usage").insert(data).execute()
        except Exception as e:
            logger.error(f"Failed to save token usage: {e}")

    def _clean_text(self, text: str) -> str:
        """
        Removes null bytes and excessive control characters to prevent AI confusion.
        """
        if not text:
            return ""
        # Remove null bytes
        text = text.replace("\x00", "")
        # Remove other control characters (except newlines/tabs)
        text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
        return text

    async def analyze_notice(
        self, text: str, site_key: str = "yu_news", title: str = "", author: str = ""
    ) -> Dict[str, Any]:
        """
        Analyzes notice text to extract summary, category, tags, and metadata.

        Args:
            text: Notice content to analyze
            site_key: Site identifier for tag selection
            title: Notice title (critical for context)
            author: Notice author/department (critical for context)
        """
        if not settings.GEMINI_API_KEY:
            return {"summary": "AI Key Missing", "category": "일반", "tags": []}

        # Pre-process text to remove noise
        text = self._clean_text(text)

        # Get available tags for this site
        available_tags = settings.AVAILABLE_TAGS.get(site_key, [])
        tags_instruction = ""
        if available_tags:
            tags_list = ", ".join([f"'{tag}'" for tag in available_tags])
            tags_instruction = (
                f"  'tags': list[string] (Select 1-2 most relevant tags from: {tags_list}.\\n"
                f"    Choose tags that best describe this notice. Prioritize the single most important tag.\\n"
                f"    For example, urgent scholarship notices should have '긴급' or '장학'.),\\n"
            )

        if not self.system_prompt_template:
            logger.error("[AI] System prompt template not loaded")
            return {"summary": "System Error", "category": "일반", "tags": []}

        # Get categories for this site
        categories = settings.CATEGORY_MAP.get(site_key) or settings.CATEGORY_MAP.get("default")
        categories_str = ", ".join([f"'{c}'" for c in categories])

        try:
            prompt = self.system_prompt_template.format(
                title=title,
                author=author,
                classification_categories=categories_str,
                tags_instruction=tags_instruction,
                content=text[: constants.AI_TEXT_TRUNCATE_LIMIT],
            )
        except KeyError as e:
            logger.error(f"[AI] Prompt formatting failed: {e}")
            return {"summary": "Prompt Error", "category": "일반", "tags": []}

        # Fetch models to try
        model_list = await self._get_available_models()
        if not model_list:
             return {"summary": "AI 가용 모델 없음", "category": "일반", "tags": []}
             
        loop = asyncio.get_running_loop()
        response = None
        last_error = None

        for model_name in model_list:
            try:
                # Configure Model
                gen_model = genai.GenerativeModel(model_name)
                
                # Pro models need longer timeout
                timeout = 120 if "pro" in model_name.lower() or "3-flash" in model_name.lower() else 60
                
                logger.info(f"[AI] Trying model: {model_name} (Timeout: {timeout}s)")
                
                response = await loop.run_in_executor(
                    None,
                    lambda: gen_model.generate_content(
                        prompt,
                        generation_config={
                            "response_mime_type": "application/json"
                        },
                        request_options={'timeout': timeout}
                    ),
                )
                
                # If we got here, success!
                logger.info(f"[AI] Success with model: {model_name}")
                break
                
            except Exception as e:
                last_error = e
                err_str = str(e)
                logger.warning(f"[AI] {model_name} failed: {err_str}")
                
                if "429" in err_str:
                    # Determine block duration
                    error_type = parse_error_type(err_str)
                    await self._block_model(model_name, error_type)
                else:
                    # Other errors (500, 503, etc) -> Maybe short block?
                    # For now, just skip without blocking (or block short)
                    pass
                
                continue # Try next model

        if not response:
            logger.error(f"[AI] All models failed. Last error: {last_error}")
            return {"summary": "AI 분석 실패 (All Models Failed)", "category": "일반", "tags": []}

            # Token Tracking
            try:
                usage = response.usage_metadata
                await self._save_token_usage(
                    usage.prompt_token_count, usage.candidates_token_count
                )
            except Exception:
                pass

            response_text = response.text
            # Log raw response for debugging (DEBUG level)
            logger.debug(f"[AI] Raw Response for {title}: {response_text}")

            return json.loads(response_text)

    async def get_diff_summary(self, old_text: str, new_text: str) -> str:
        """
        Generates a summary of changes between old and new text.
        """
        if not settings.GEMINI_API_KEY:
            return "내용 변경 (AI Key Missing)"

        prompt = (
            "Compare the following two versions of a notice and summarize the changes in Korean.\n"
            "Output ONLY the summary of what changed (e.g., '신청 기간이 11/25에서 11/30으로 연장되었습니다.').\n"
            "If the changes are only whitespace, formatting, or semantically identical, output 'NO_CHANGE'.\n"
            "Keep it concise (1 sentence).\n\n"
            f"--- OLD VERSION ---\n{old_text[:2000]}\n\n"
            f"--- NEW VERSION ---\n{new_text[:2000]}"
        )

        try:
            loop = asyncio.get_running_loop()
            # Use a cheap/fast model for diffs
            model = genai.GenerativeModel("gemini-2.5-flash-lite") 
            response = await loop.run_in_executor(
                None, lambda: model.generate_content(prompt)
            )

            try:
                usage = response.usage_metadata
                await self._save_token_usage(
                    usage.prompt_token_count, usage.candidates_token_count
                )
            except Exception:
                pass

            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Diff failed: {e}")
            return "내용 변경 (AI 분석 실패)"

    async def extract_menu_from_image(self, image_url: str, image_data: bytes = None) -> Dict[str, Any]:
        """
        Extracts menu text from an image URL or provided bytes using Gemini Vision.
        Returns JSON with 'raw_text', 'start_date', 'end_date'.
        """
        if not settings.GEMINI_API_KEY:
            return {}

        import aiohttp

        try:
            # 1. Download Image (if not provided)
            if not image_data:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            logger.error(
                                f"[AI] Failed to download menu image: {resp.status}"
                            )
                            return {}
                        image_data = await resp.read()

            # 2. Prepare Prompt
            prompt = (
                "Extract the weekly meal plan from this image. Respond in JSON format.\n"
                "1. 'raw_text': string. A clean, formatted text representation of the menu for the whole week.\n"
                "   - Group by Date (e.g., '## 11월 25일 (월)').\n"
                "   - List Breakfast/Lunch/Dinner clearly.\n"
                "2. 'start_date': string (YYYY-MM-DD). The first date in the menu.\n"
                "3. 'end_date': string (YYYY-MM-DD). The last date in the menu.\n"
            )

            # 3. Call Gemini Vision
            loop = asyncio.get_running_loop()
            # Use 2.5-flash for vision capabilities
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    [prompt, {"mime_type": "image/jpeg", "data": image_data}],
                    generation_config={"response_mime_type": "application/json"},
                ),
            )

            # 4. Token Tracking
            try:
                usage = response.usage_metadata
                await self._save_token_usage(
                    usage.prompt_token_count, usage.candidates_token_count
                )
            except Exception:
                pass

            return json.loads(response.text)

        except Exception as e:
            logger.error(f"[AI] Menu extraction failed: {e}")
            return {}

    async def get_embedding(self, text: str) -> list:
        if not settings.GEMINI_API_KEY:
            return []
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text[:9000],
                task_type="retrieval_document",
                title="University Notice",
            )
            return result["embedding"]
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return []
