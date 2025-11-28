import google.generativeai as genai
from typing import Dict, Any, Optional
import json
import asyncio
import time
import random
import re
from core.config import settings
from core.logger import get_logger
from core.database import Database

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

    async def _save_token_usage(self, prompt_tokens: int, completion_tokens: int):
        if not self.db: return
        try:
            data = {
                'model': settings.GEMINI_MODEL,
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'total_tokens': prompt_tokens + completion_tokens
            }
            self.db.table('token_usage').insert(data).execute()
        except Exception as e:
            logger.error(f"Failed to save token usage: {e}")

    async def analyze_notice(self, text: str) -> Dict[str, Any]:
        """
        Analyzes notice text to extract summary, category, and metadata.
        """
        if not self.model:
            return {"summary": "AI Key Missing", "category": "일반"}

        prompt = (
            "Analyze this university notice. Respond in JSON format.\n\n"
            "1. 'category': string. Choose one: '장학', '학사', '취업', 'dormitory', '일반'.\n"
            "2. 'summary': string. Summarize concisely in Korean (3 lines max).\n"
            "   - End sentences with noun-endings (~함).\n"
            "   - Use structured format ONLY if applicable (e.g., '- 일시: ...').\n"
            "   - Otherwise, use natural bullet points starting with a hyphen (-).\n"
            "3. 'start_date': string (optional, YYYY-MM-DD).\n"
            "4. 'end_date': string (optional, YYYY-MM-DD).\n"
            "5. 'target_grades': list of integers (optional, [1,2,3,4]).\n"
            "6. 'target_dept': string (optional).\n\n"
            f"Content:\n{text[:4000]}"
        )

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
                            generation_config={"response_mime_type": "application/json"}
                        )
                    )
                    break # Success
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
                            wait_time = (2 ** attempt) * 2 + random.uniform(0, 1)
                        else:
                            wait_time += 1.0 # Add 1s buffer
                        
                        # Cap maximum wait time to 60 seconds
                        if wait_time > 60:
                            logger.warning(f"[AI] Rate limit requires {wait_time:.0f}s wait, capping at 60s")
                            wait_time = 60
                        
                        if attempt < max_retries - 1:
                            logger.warning(f"[AI] Rate limit hit (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time:.1f}s...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"[AI] Rate limit - max retries ({max_retries}) reached. Skipping analysis.")
                            return {"summary": "AI 분석 실패 (Rate Limit)", "category": "일반"}
                    else:
                        raise e
            
            if not response:
                raise Exception("Max retries exceeded for AI analysis")

            # Rate Limiting (Safety Delay) - reduced since scraper handles rate limiting
            await asyncio.sleep(1.0) 
            
            # Token Tracking
            try:
                usage = response.usage_metadata
                await self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
            except: pass

            return json.loads(response.text)
        except Exception as e:
            logger.error(f"[AI] Analysis failed: {e}")
            return {"summary": "AI Analysis Failed", "category": "일반"}

    async def get_diff_summary(self, old_text: str, new_text: str) -> str:
        """
        Generates a summary of changes between old and new text.
        """
        if not self.model: return "내용 변경 (AI Key Missing)"

        prompt = (
            "Compare the following two versions of a notice and summarize the changes in Korean.\n"
            "Output ONLY the summary of what changed (e.g., '신청 기간이 11/25에서 11/30으로 연장되었습니다.').\n"
            "Keep it concise (1 sentence).\n\n"
            f"--- OLD VERSION ---\n{old_text[:2000]}\n\n"
            f"--- NEW VERSION ---\n{new_text[:2000]}"
        )

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self.model.generate_content(prompt))
            
            try:
                usage = response.usage_metadata
                await self._save_token_usage(usage.prompt_token_count, usage.candidates_token_count)
            except: pass

            return response.text.strip()
        except Exception as e:
            logger.error(f"AI Diff failed: {e}")
            return "내용 변경 (AI 분석 실패)"

    async def get_embedding(self, text: str) -> list:
        if not settings.GEMINI_API_KEY: return []
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text[:9000],
                task_type="retrieval_document",
                title="University Notice"
            )
            return result['embedding']
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return []
