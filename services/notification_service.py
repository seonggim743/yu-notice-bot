import aiohttp
import json
import asyncio
import html
from datetime import datetime
from typing import List, Dict, Optional
from core.config import settings
from core.logger import get_logger
from core.performance import get_performance_monitor
from models.notice import Notice

logger = get_logger(__name__)

class NotificationService:
    def __init__(self):
        self.telegram_token = settings.TELEGRAM_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    async def send_telegram(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str = "") -> Optional[int]:
        """
        Sends a notice to Telegram with enhanced formatting. Returns the Message ID.
        """
        if not self.telegram_token: return None

        # Source-based Routing
        topic_id = settings.TELEGRAM_TOPIC_MAP.get(notice.site_key)
        
        # Category Emojis
        cat_emojis = {
            "장학": "💰",
            "학사": "🎓",
            "취업": "💼",
            "dormitory": "🏠",
            "일반": "📢"
        }
        cat_emoji = cat_emojis.get(notice.category, "📢")
        
        # Status Prefix
        prefix = "🆕" if is_new else "🔄"
        
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
        
        # Enhanced Message Format
        msg = (
            f"{prefix} <b>{cat_emoji} {safe_title}</b>\n\n"
            f"{safe_summary}\n\n"
        )
        
        if modified_reason:
            msg += f"⚠️ <b>수정 사항</b>: {modified_reason}\n\n"
            
        msg += f"🔗 <a href='{notice.url}'>공지사항 보러가기</a>\n"
        msg += f"{hashtag}"

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

        # Prepare base payload
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
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

        main_msg_id = None
        
        # 1. Try Send Image (if exists)
        if notice.image_url:
            try:
                # Download image with Referer to bypass anti-hotlinking
                headers = {
                    'Referer': notice.url,
                    'User-Agent': 'Mozilla/5.0'
                }
                async with session.get(notice.image_url, headers=headers) as resp:
                    if resp.status == 200:
                        photo_data = await resp.read()
                        
                        # Telegram Caption Limit is 1024 chars
                        caption_text = msg
                        if len(caption_text) > 1024:
                            logger.warning(f"[NOTIFIER] Caption too long ({len(caption_text)}), truncating for photo.")
                            caption_text = caption_text[:1020] + "..."
                        
                        photo_payload = {
                            'chat_id': self.chat_id,
                            'caption': caption_text,
                            'parse_mode': 'HTML',
                            'disable_web_page_preview': 'true'
                        }
                        if topic_id:
                            photo_payload['message_thread_id'] = topic_id
                        if buttons:
                            photo_payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})

                        # Send photo as multipart/form-data
                        form = aiohttp.FormData()
                        form.add_field('photo', photo_data, filename='image.jpg', content_type='image/jpeg')
                        for key, value in photo_payload.items():
                            if isinstance(value, dict):
                                form.add_field(key, json.dumps(value))
                            else:
                                form.add_field(key, str(value))

                        with get_performance_monitor().measure("send_telegram_photo", {"title": notice.title}):
                            async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto", data=form) as photo_resp:
                                if photo_resp.status == 200:
                                    result = await photo_resp.json()
                                    main_msg_id = result.get('result', {}).get('message_id')
                                    logger.info(f"[NOTIFIER] Telegram photo sent: {notice.title}")
                                else:
                                    error_text = await photo_resp.text()
                                    logger.warning(f"[NOTIFIER] Failed to send photo ({photo_resp.status}): {error_text}, falling back to text message.")
                    else:
                        logger.warning(f"[NOTIFIER] Failed to download image ({resp.status}) from {notice.image_url}, falling back to text message.")
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending photo: {e}, falling back to text message.")
        
        # 2. Fallback: Send Text Message if photo wasn't sent
        if not main_msg_id:
            try:
                with get_performance_monitor().measure("send_telegram_message", {"title": notice.title}):
                    async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendMessage", json=payload) as resp:
                        resp.raise_for_status()
                        result = await resp.json()
                        main_msg_id = result.get('result', {}).get('message_id')
                        logger.info(f"[NOTIFIER] Telegram message sent: {notice.title}")
            except aiohttp.ClientError as e:
                logger.error(f"[NOTIFIER] Telegram send failed (HTTP {getattr(e, 'status', 'N/A')}): {e}")
                return None
            except Exception as e:
                logger.error(f"[NOTIFIER] Telegram send failed: {e}")
                return None

        # Send Files as Media Group (grouped reply)
        if main_msg_id and notice.attachments:
            logger.info(f"[NOTIFIER] Attempting to send {len(notice.attachments)} attachments as media group")
            
            # Download all files first
            downloaded_files = []
            
            for idx, att in enumerate(notice.attachments, 1):
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    try:
                        logger.info(f"[NOTIFIER] Downloading attachment {idx}/{len(notice.attachments)}: {att.name} (attempt {attempt}/{max_retries})")
                        
                        # Download file with proper headers
                        headers = {
                            'Referer': notice.url,
                            'User-Agent': settings.USER_AGENT,
                            'Accept': '*/*',
                            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                            'Connection': 'keep-alive'
                        }
                        
                        async with session.get(att.url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                file_data = await resp.read()
                                file_size = len(file_data)
                                
                                logger.info(f"[NOTIFIER] Downloaded {att.name}: {file_size} bytes")
                                
                                # Telegram file size limit: 50MB for bots
                                if file_size > 50 * 1024 * 1024:
                                    logger.warning(f"[NOTIFIER] File {att.name} too large ({file_size} bytes), skipping")
                                    break
                                
                                # Get actual filename from Content-Disposition if available
                                actual_filename = att.name
                                if 'Content-Disposition' in resp.headers:
                                    import re
                                    from urllib.parse import unquote
                                    cd = resp.headers['Content-Disposition']
                                    # Try to extract filename from Content-Disposition header
                                    match = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)', cd)
                                    if match:
                                        actual_filename = unquote(match.group(1))
                                        logger.info(f"[NOTIFIER] Extracted filename from header: {actual_filename}")
                                
                                downloaded_files.append({
                                    'data': file_data,
                                    'filename': actual_filename,
                                    'original_name': att.name
                                })
                                logger.info(f"[NOTIFIER] Successfully downloaded: {actual_filename}")
                                break  # Success, exit retry loop
                                
                            elif resp.status == 404:
                                logger.error(f"[NOTIFIER] File not found (404): {att.url}")
                                break  # Don't retry on 404
                            elif resp.status == 403:
                                logger.error(f"[NOTIFIER] Access forbidden (403): {att.url}")
                                break  # Don't retry on 403
                            else:
                                logger.error(f"[NOTIFIER] HTTP {resp.status} downloading {att.name} from {att.url}")
                                if attempt < max_retries and resp.status >= 500:
                                    await asyncio.sleep(2)
                                    continue
                                else:
                                    break
                    except asyncio.TimeoutError:
                        logger.error(f"[NOTIFIER] Timeout downloading {att.name} (attempt {attempt}/{max_retries})")
                        if attempt < max_retries:
                            await asyncio.sleep(2)
                            continue
                    except Exception as e:
                        logger.error(f"[NOTIFIER] Error processing file {att.name} (attempt {attempt}/{max_retries}): {e}", exc_info=True)
                        if attempt < max_retries:
                            await asyncio.sleep(2)
                            continue
                        break
            
            # Send all downloaded files as a media group
            if downloaded_files:
                try:
                    logger.info(f"[NOTIFIER] Sending {len(downloaded_files)} files as media group")
                    
                    # Telegram media group limit: 10 files
                    if len(downloaded_files) > 10:
                        logger.warning(f"[NOTIFIER] Too many files ({len(downloaded_files)}), splitting into batches of 10")
                    
                    # Process in batches of 10
                    for batch_idx in range(0, len(downloaded_files), 10):
                        batch = downloaded_files[batch_idx:batch_idx + 10]
                        
                        # Build media array for sendMediaGroup
                        media = []
                        form = aiohttp.FormData()
                        
                        for idx, file_info in enumerate(batch):
                            # Add file to form data
                            field_name = f"file{idx}"
                            form.add_field(
                                field_name,
                                file_info['data'],
                                filename=file_info['filename'],
                                content_type='application/octet-stream'
                            )
                            
                            # Add to media array (reference the uploaded file)
                            media_item = {
                                "type": "document",
                                "media": f"attach://{field_name}",
                                "caption": file_info['filename'] if idx == 0 else ""  # Only first file gets caption
                            }
                            media.append(media_item)
                        
                        # Add other required fields
                        form.add_field('chat_id', str(self.chat_id))
                        form.add_field('media', json.dumps(media))
                        form.add_field('reply_to_message_id', str(main_msg_id))
                        if topic_id:
                            form.add_field('message_thread_id', str(topic_id))
                        
                        # Send media group
                        async with session.post(
                            f"https://api.telegram.org/bot{self.telegram_token}/sendMediaGroup",
                            data=form,
                            timeout=aiohttp.ClientTimeout(total=120)
                        ) as resp:
                            if resp.status == 200:
                                logger.info(f"[NOTIFIER] Successfully sent media group batch {batch_idx//10 + 1} ({len(batch)} files)")
                            else:
                                error_text = await resp.text()
                                logger.error(f"[NOTIFIER] Failed to send media group: {resp.status} - {error_text}")
                                
                except Exception as e:
                    logger.error(f"[NOTIFIER] Error sending media group: {e}", exc_info=True)
            else:
                logger.warning(f"[NOTIFIER] No files were successfully downloaded")

        return main_msg_id

    async def send_discord(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str = "", max_retries: int = 2):
        """
        Sends a notice to Discord via Webhook with enhanced Embed.
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
            "author": {
                "name": "Yu Notice Bot",
                "icon_url": "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png"
            },
            "footer": {
                "text": f"Category: {notice.category} • {notice.site_key}"
            },
            "timestamp": datetime.utcnow().isoformat(),
            "fields": []
        }
        
        if notice.image_url:
            embed["image"] = {"url": notice.image_url}
        
        if modified_reason:
            embed["fields"].append({
                "name": "⚠️ 수정 사항",
                "value": modified_reason,
                "inline": False
            })
            
        if notice.attachments:
            if len(notice.attachments) > 5:
                logger.warning(f"[NOTIFIER] Notice has {len(notice.attachments)} attachments, only showing first 5 in Discord")
            file_links = [f"[{a.name}]({a.url})" for a in notice.attachments[:5]]
            embed["fields"].append({
                "name": "📎 첨부파일",
                "value": "\n".join(file_links), 
                "inline": False
            })

        # Validate embed size (Discord limit: 6000 chars for description)
        if len(embed.get("description", "")) > 4000:
            logger.warning(f"[NOTIFIER] Summary too long ({len(embed['description'])} chars), truncating...")
            embed["description"] = embed["description"][:3950] + "...\n\n(내용이 잘렸습니다)"

        payload = {"embeds": [embed]}
        
        # Retry logic for transient failures
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"[NOTIFIER] Sending Discord webhook (attempt {attempt}/{max_retries})")
                
                with get_performance_monitor().measure("send_discord", {"title": notice.title, "attempt": attempt}):
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

    async def send_menu_notification(self, session: aiohttp.ClientSession, notice: Notice, menu_data: Dict[str, Any]):
        """
        Sends extracted menu text to Telegram and Pins it.
        """
        if not self.telegram_token: return

        # 1. Construct Message
        raw_text = menu_data.get('raw_text', '식단 정보 없음')
        start_date = menu_data.get('start_date', '')
        end_date = menu_data.get('end_date', '')
        
        msg = (
            f"🍱 <b>주간 기숙사 식단표</b>\n"
            f"📅 기간: {start_date} ~ {end_date}\n\n"
            f"{html.escape(raw_text)}\n\n"
            f"#Menu #식단"
        )
        
        # 2. Send to Telegram
        topic_id = settings.TELEGRAM_TOPIC_MAP.get(notice.site_key)
        payload = {
            'chat_id': self.chat_id,
            'text': msg,
            'parse_mode': 'HTML'
        }
        if topic_id: payload['message_thread_id'] = topic_id
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                result = await resp.json()
                msg_id = result.get('result', {}).get('message_id')
                
                if msg_id:
                    logger.info(f"[NOTIFIER] Menu sent to Telegram: {msg_id}")
                    
                    # 3. Pin Message
                    pin_payload = {'chat_id': self.chat_id, 'message_id': msg_id}
                    async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/pinChatMessage", json=pin_payload) as pin_resp:
                        if pin_resp.status == 200:
                            logger.info(f"[NOTIFIER] Menu pinned successfully")
                        else:
                            logger.warning(f"[NOTIFIER] Failed to pin menu: {await pin_resp.text()}")
                            
        except Exception as e:
            logger.error(f"[NOTIFIER] Failed to send/pin menu: {e}")

        # 4. Send to Discord (Optional, just text embed)
        # Reuse send_discord logic or create custom embed if needed
        # For now, we rely on the main notice notification for Discord which includes the image.
        # We can add a follow-up text embed if requested.
