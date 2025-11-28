import aiohttp
import json
import asyncio
import html
from typing import List, Dict, Optional
from core.config import settings
from core.logger import get_logger
from models.notice import Notice

logger = get_logger(__name__)

class NotificationService:
    def __init__(self):
        self.telegram_token = settings.TELEGRAM_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    async def send_telegram(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str = "") -> Optional[int]:
        """
        Sends a notice to Telegram. Returns the Message ID.
        """
        if not self.telegram_token: return None

        # Source-based Routing
        topic_id = settings.TELEGRAM_TOPIC_MAP.get(notice.site_key)
        
        # Emoji & Prefix
        prefix = "🆕" if is_new else "🔄"
        status = "새 공지" if is_new else "수정된 공지"
        
        # Content Construction
        safe_title = html.escape(notice.title)
        safe_summary = html.escape(notice.summary)
        
        # Hashtags Mapping
        category_map = {
            "장학": "#Scholarship #장학",
            "학사": "#Academic #학사",
            "취업": "#Job #취업",
            "dormitory": "#Dormitory #생활관",
            "일반": "#General #일반"
        }
        hashtag = category_map.get(notice.category, "#General #일반")
        
        msg = (
            f"{prefix} <b><a href='{notice.url}'>{safe_title}</a></b>\n\n"
            f"📝 <b>요약</b>\n{safe_summary}"
        )
        
        if modified_reason:
            msg += f"\n\n⚠️ <b>수정 사항</b>: {modified_reason}"
            
        msg += f"\n\n{hashtag}"

        # Buttons (Download Links)
        buttons = []
        if notice.attachments:
            for att in notice.attachments:
                fname = att.name
                if len(fname) > 20: fname = fname[:17] + "..."
                buttons.append({"text": f"📥 {fname}", "url": att.url})
        
        # Payload for Main Message
        payload = {
            'chat_id': self.chat_id,
            'text': msg,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true'
        }
        
        if topic_id:
            payload['message_thread_id'] = topic_id
            
        if buttons:
            inline_keyboard = [[{"text": b['text'], "url": b['url']}] for b in buttons]
            payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        
        main_msg_id = None
        try:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                result = await resp.json()
                main_msg_id = result.get('result', {}).get('message_id')
                logger.info(f"[NOTIFIER] Telegram sent: {notice.title}")
        except aiohttp.ClientError as e:
            logger.error(f"[NOTIFIER] Telegram send failed (HTTP {getattr(e, 'status', 'N/A')}): {e}")
            return None
        except Exception as e:
            logger.error(f"[NOTIFIER] Telegram send failed: {e}")
            return None

        # Send Files (Best Effort)
        if main_msg_id and notice.attachments:
            for att in notice.attachments:
                try:
                    # Send as document using URL (Telegram downloads it)
                    # Note: If URL is protected, this fails. Assuming public URLs for now.
                    doc_payload = {
                        'chat_id': self.chat_id,
                        'document': att.url,
                        'caption': att.name,
                        'reply_to_message_id': main_msg_id
                    }
                    if topic_id: doc_payload['message_thread_id'] = topic_id
                    
                    async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendDocument", json=doc_payload) as resp:
                        if resp.status != 200:
                            logger.warning(f"[NOTIFIER] Failed to send file {att.name}: {await resp.text()}")
                except Exception as e:
                    logger.error(f"[NOTIFIER] File send error: {e}")

        return main_msg_id

    async def send_discord(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str = "", max_retries: int = 2):
        """
        Sends a notice to Discord via Webhook.
        """
        webhook_url = settings.DISCORD_WEBHOOK_MAP.get(notice.site_key)
        if not webhook_url:
            logger.warning(f"[NOTIFIER] No Discord Webhook found for key '{notice.site_key}'. Available: {list(settings.DISCORD_WEBHOOK_MAP.keys())}")
            return

        # Color Mapping
        colors = {
            "장학": 0xFFD700,      # Gold
            "학사": 0x5865F2,      # Blurple
            "취업": 0x57F287,      # Green
            "dormitory": 0xEB459E, # Pink
            "일반": 0x99AAB5       # Gray
        }
        color = colors.get(notice.category, 0x99AAB5)

        prefix = "🆕" if is_new else "🔄"
        
        embed = {
            "title": f"{prefix} {notice.title}",
            "url": notice.url,
            "description": notice.summary,
            "color": color,
            "fields": [
                {"name": "Category", "value": notice.category, "inline": True}
            ]
        }
        
        if notice.image_url:
            embed["image"] = {"url": notice.image_url}
        
        if modified_reason:
            embed["fields"].append({
                "name": "수정 사항",
                "value": modified_reason,
                "inline": False
            })
            
        if notice.attachments:
            if len(notice.attachments) > 5:
                logger.warning(f"[NOTIFIER] Notice has {len(notice.attachments)} attachments, only showing first 5 in Discord")
            file_links = [f"[{a.name}]({a.url})" for a in notice.attachments[:5]]
            embed["fields"].append({
                "name": "첨부파일 (다운로드)",
                "value": "\n".join(file_links), 
                "inline": False
            })

        # Validate embed size (Discord limit: 6000 chars for description)
        if len(embed.get("description", "")) > 6000:
            logger.warning(f"[NOTIFIER] Summary too long ({len(embed['description'])} chars), truncating...")
            embed["description"] = embed["description"][:5950] + "...\n\n(내용이 잘렸습니다)"

        payload = {"embeds": [embed]}
        
        # Retry logic for transient failures
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"[NOTIFIER] Sending Discord webhook (attempt {attempt}/{max_retries})")
                async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status not in [200, 204]:
                        error_text = await resp.text()
                        logger.error(f"[NOTIFIER] Discord send failed: {resp.status} - {error_text}")
                        if attempt < max_retries and resp.status >= 500:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                    else:
                        logger.info(f"[NOTIFIER] Discord sent: {notice.title}")
                        return
            except asyncio.TimeoutError:
                logger.error(f"[NOTIFIER] Discord webhook timeout (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                logger.error(f"[NOTIFIER] Discord send failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
        
        logger.error(f"[NOTIFIER] Discord send failed after {max_retries} attempts")
