import asyncio
import aiohttp
import hashlib
from typing import List, Dict, Optional
from core.config import settings
from core.logger import get_logger
from models.notice import Notice
from repositories.notice_repo import NoticeRepository
from services.ai_service import AIService
from services.notification_service import NotificationService
from parsers.html_parser import HTMLParser
from core.performance import get_performance_monitor

logger = get_logger(__name__)

class ScraperService:
    def __init__(self):
        self.repo = NoticeRepository()
        self.ai = AIService()
        self.notifier = NotificationService()
        
        # Safety Limits
        self.ai_summary_count = 0
        self.MAX_AI_SUMMARIES = 10
        
        # Rate Limiting (Gemini 2.5 Flash: 10 RPM = 6 seconds per request)
        # Using 7s for safety margin + each notice has multiple AI calls
        self.AI_CALL_DELAY = 7.0  # 7 seconds between AI calls
        self.NOTICE_PROCESS_DELAY = 0.5  # 0.5 seconds between each notice
        
        # Define Targets (Hardcoded for now, could be in config/DB)
        self.targets = [
            {
                "key": "yu_news",
                "url": "https://www.yu.ac.kr/main/intro/yu-news.do",
                "base_url": "https://www.yu.ac.kr",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-content-box")
            },
            {
                "key": "cse_notice",
                "url": "https://www.yu.ac.kr/cse/community/notice.do",
                "base_url": "https://www.yu.ac.kr",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-content-box")
            },
            {
                "key": "bachelor_guide",
                "url": "https://www.yu.ac.kr/main/bachelor/bachelor-guide.do",
                "base_url": "https://www.yu.ac.kr",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-content-box")
            },
            {
                "key": "dormitory_notice",
                "url": "https://www.yu.ac.kr/dormi/community/notice.do",
                "base_url": "https://www.yu.ac.kr",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-content-box")
            }
        ]

    def calculate_hash(self, notice: Notice) -> str:
        """Hash of Title + Content + Attachments (sorted for stability)"""
        sorted_atts = sorted([a.name for a in notice.attachments])
        att_str = "".join(sorted_atts)
        raw = f"{notice.title}{notice.content}{att_str}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def process_target(self, session: aiohttp.ClientSession, target: Dict):
        key = target['key']
        monitor = get_performance_monitor()
        
        with monitor.measure("scrape_target", {"key": key}):
            logger.info(f"[SCRAPER] Scraping {key}...")
        
        try:
            async with session.get(target['url'], timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                html = await resp.text()
        except asyncio.TimeoutError:
            logger.error(f"[SCRAPER] Timeout fetching {key} after 30s")
            return
        except aiohttp.ClientError as e:
            logger.error(f"[SCRAPER] HTTP error fetching {key}: {e}")
            return
        except Exception as e:
            logger.error(f"[SCRAPER] Unexpected error fetching {key}: {e}")
            return

        parser = target['parser']
        items = parser.parse_list(html, key, target['base_url'])
        
        # IMPORTANT: Process oldest first (reverse chronological order)
        items.reverse()
        
        # Get already processed IDs
        processed_ids = self.repo.get_last_processed_ids(key, limit=1000)
        
        for item in items:
            is_new = item.article_id not in processed_ids
            old_hash = processed_ids.get(item.article_id)
            
            # Fetch detail
            try:
                async with session.get(item.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    detail_html = await resp.text()
            except asyncio.TimeoutError:
                logger.warning(f"[SCRAPER] Timeout fetching detail for {item.title}")
                continue
            except Exception as e:
                logger.warning(f"[SCRAPER] Failed to fetch detail for {item.title}: {e}")
                continue
            
            item = parser.parse_detail(detail_html, item)
            
            # Detect empty content
            if not item.content or len(item.content.strip()) < 10:
                logger.warning(f"[SCRAPER] Empty or very short content for '{item.title}'. Skipping.")
                continue
            
            logger.info(f"[SCRAPER] Content length for '{item.title}': {len(item.content)}")
            current_hash = self.calculate_hash(item)
            item.content_hash = current_hash
            
            is_modified = False
            modified_reason = ""
            
            if not is_new:
                if old_hash == current_hash:
                    continue  # No change
                else:
                    is_modified = True
                    modified_reason = "내용 또는 제목 변경됨"
            
            logger.info(f"Processing {'New' if is_new else 'Modified'}: {item.title}")
            
            # AI Analysis with rate limiting
            if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                logger.info(f"[SCRAPER] Starting AI analysis ({self.ai_summary_count + 1}/{self.MAX_AI_SUMMARIES})...")
                
                # 1. Analyze content (Wait BEFORE first call)
                logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before analyze_notice...")
                await asyncio.sleep(self.AI_CALL_DELAY)
                
                with monitor.measure("ai_analysis", {"type": "summary", "title": item.title}):
                    analysis = await self.ai.analyze_notice(item.content)
                
                item.category = analysis.get('category', '일반')
                item.summary = analysis.get('summary', item.content[:100])
                
                # 2. Get embedding (Wait BEFORE second call)
                logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_embedding...")
                await asyncio.sleep(self.AI_CALL_DELAY)
                
                item.embedding = await self.ai.get_embedding(f"{item.title}\n{item.summary}")
                self.ai_summary_count += 1
                
                logger.info(f"[SCRAPER] AI complete. Quota: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}")
            else:
                logger.warning(f"[SCRAPER] AI limit reached. Skipping AI analysis.")
                item.category = '일반'
                item.summary = item.content[:100] + " (AI 한도 도달)"
                item.embedding = []
            
            # AI Diff for Modified
            if is_modified:
                old_notice = self.repo.get_notice(key, item.article_id)
                
                changes = {}
                if old_notice:
                    if old_notice.title != item.title:
                        changes['title'] = f"'{old_notice.title}' -> '{item.title}'"
                    
                    if old_notice.content != item.content:
                        if self.ai_summary_count < self.MAX_AI_SUMMARIES:
                            logger.info(f"[SCRAPER] Waiting {self.AI_CALL_DELAY}s before get_diff_summary...")
                            await asyncio.sleep(self.AI_CALL_DELAY)
                            changes['content'] = await self.ai.get_diff_summary(old_notice.content, item.content)
                            self.ai_summary_count += 1  # Count diff as an AI call
                        else:
                            changes['content'] = "내용 변경됨 (AI 한도 초과)"
                    
                    # Attachments
                    old_atts = {a.name for a in old_notice.attachments}
                    new_atts = {a.name for a in item.attachments}
                    added = new_atts - old_atts
                    removed = old_atts - new_atts
                    if added or removed:
                        changes['attachments'] = f"Added: {', '.join(added)}, Removed: {', '.join(removed)}"
                
                item.change_details = changes
                
                # Construct readable reason
                reasons = []
                if 'title' in changes: reasons.append("제목 변경")
                if 'content' in changes: reasons.append(f"내용 변경: {changes['content']}")
                if 'attachments' in changes: reasons.append("첨부파일 변경")
                
                modified_reason = ", ".join(reasons) if reasons else "내용 변경됨"

            # Save to DB
            notice_id = self.repo.upsert_notice(item)
            
            if notice_id:
                # Notify
                msg_id = await self.notifier.send_telegram(session, item, is_new, modified_reason)
                if msg_id:
                    self.repo.update_message_ids(notice_id, 'telegram', msg_id)
                    
                await self.notifier.send_discord(session, item, is_new, modified_reason)
            
            # Small delay between notices
            await asyncio.sleep(self.NOTICE_PROCESS_DELAY)

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={'User-Agent': settings.USER_AGENT}
        ) as session:
            monitor = get_performance_monitor()
            
            with monitor.measure("full_scrape_run"):
                logger.info(f"[SCRAPER] Processing {len(self.targets)} targets sequentially...")
                
                for target in self.targets:
                    try:
                        await self.process_target(session, target)
                    except Exception as e:
                        logger.error(f"[SCRAPER] Target {target['key']} failed: {e}")
            
            logger.info(f"[SCRAPER] Complete. Total AI calls: {self.ai_summary_count}/{self.MAX_AI_SUMMARIES}")
            monitor.log_summary()
