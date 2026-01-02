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

logger = get_logger(__name__)


class AIService:
    def __init__(self):
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self.model = genai.GenerativeModel(settings.GEMINI_MODEL)
            self.db = Database.get_client()
        else:
            logger.warning("[AI] Gemini API Key missing. AI features disabled.")
            self.model = None

        self.system_prompt_template = self._load_system_prompt()

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
        if not self.model:
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

        try:
            loop = asyncio.get_running_loop()
            response = None

            # Retry Logic with max attempts
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = await loop.run_in_executor(
                        None,
                        lambda: self.model.generate_content(
                            prompt,
                            generation_config={
                                "response_mime_type": "application/json"
                            },
                        ),
                    )
                    break  # Success
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str:
                        # Try to parse "retry in X seconds" or "retry_delay { seconds: 57 }"
                        wait_time = 0

                        match = re.search(r"retry in (\d+(\.\d+)?)s", err_str)
                        if match:
                            wait_time = float(match.group(1))
                        else:
                            match = re.search(r"seconds:\s*(\d+)", err_str)
                            if match:
                                wait_time = float(match.group(1))

                        # Fallback if parsing failed
                        if wait_time == 0:
                            wait_time = (2**attempt) * 2 + random.uniform(0, 1)
                        else:
                            wait_time += 1.0  # Add 1s buffer

                        # Cap maximum wait time to 60 seconds
                        if wait_time > 60:
                            logger.warning(
                                f"[AI] Rate limit requires {wait_time:.0f}s wait, capping at 60s"
                            )
                            wait_time = 60

                        if attempt < max_retries - 1:
                            logger.warning(
                                f"[AI] Rate limit hit (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time:.1f}s..."
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(
                                f"[AI] Rate limit - max retries ({max_retries}) reached. Skipping analysis."
                            )
                            return {
                                "summary": "AI 분석 실패 (Rate Limit)",
                                "category": "일반",
                                "tags": [],
                            }
                    else:
                        raise e

            if not response:
                raise Exception("Max retries exceeded for AI analysis")

            # Rate Limiting (Safety Delay) - reduced since scraper handles rate limiting
            await asyncio.sleep(1.0)

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
        except Exception as e:
            logger.error(f"[AI] Analysis failed: {e}")
            return {"summary": "AI Analysis Failed", "category": "일반", "tags": []}

    async def get_diff_summary(self, old_text: str, new_text: str) -> str:
        """
        Generates a summary of changes between old and new text.
        """
        if not self.model:
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
            response = await loop.run_in_executor(
                None, lambda: self.model.generate_content(prompt)
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
        if not self.model:
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
            response = await loop.run_in_executor(
                None,
                lambda: self.model.generate_content(
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
