import aiohttp
import json
import asyncio
import html
from aiohttp import MultipartWriter
from aiohttp.payload import BytesPayload, StringPayload
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
                                "media": f"attach://{field_name}"
                                # No caption - filename is already shown in the file itself
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
        Sends a notice to Discord via Bot API (preferred) or Webhook.
        """
        # Load token directly from .env to ensure consistency with working test script
        from dotenv import load_dotenv
        import os
        load_dotenv()
        
        bot_token = os.getenv('DISCORD_BOT_TOKEN')
        channel_map_str = os.getenv('DISCORD_CHANNEL_MAP')
        
        # 1. Bot API (Priority)
        if bot_token and channel_map_str:
            try:
                import json
                channel_map = json.loads(channel_map_str)
                channel_id = channel_map.get(notice.site_key)
                
                if channel_id:
                    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                    headers = {
                        "Authorization": f"Bot {bot_token}",
                        # No explicit User-Agent to match working test script
                    }
                    
                    return await self._send_discord_common(session, notice, is_new, modified_reason, url, headers, max_retries)
                else:
                    logger.warning(f"[NOTIFIER] No Discord channel found for key '{notice.site_key}' (Bot API configured)")
            except json.JSONDecodeError:
                logger.error("[NOTIFIER] Invalid JSON in DISCORD_CHANNEL_MAP")

        # 2. Webhook (Legacy) - Only if Bot API is not used
        if settings.DISCORD_WEBHOOK_MAP:
            webhook_url = settings.DISCORD_WEBHOOK_MAP.get(notice.site_key)
            if webhook_url:
                return await self._send_discord_common(session, notice, is_new, modified_reason, webhook_url, {}, max_retries)
            else:
                logger.warning(f"[NOTIFIER] No Discord Webhook found for key '{notice.site_key}'")
        else:
            logger.warning(f"[NOTIFIER] No Discord configuration found for key '{notice.site_key}'")

    async def _send_discord_common(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str, url: str, headers: Dict[str, str], max_retries: int):
        """
        Common method to send Discord notifications.
        Uses a dedicated session for API calls to avoid header conflicts.
        """

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
        
        if modified_reason:
            embed["fields"].append({
                "name": "⚠️ 수정 사항",
                "value": modified_reason,
                "inline": False
            })
            
        # Download attachments using the SHARED session (to handle hotlink protection/cookies)
        attachment_files = []
        image_data = None
        image_filename = "image.png"

        if notice.attachments:
            for idx, att in enumerate(notice.attachments[:10], 1):
                try:
                    # Use shared session for download
                    async with session.get(att.url, headers={'Referer': notice.url}, timeout=aiohttp.ClientTimeout(total=30)) as file_resp:
                        if file_resp.status == 200:
                            file_data = await file_resp.read()
                            file_size = len(file_data)
                            if file_size > 25 * 1024 * 1024: continue
                            
                            actual_filename = att.name
                            # Safe filename logic
                            import re
                            try:
                                ext = actual_filename.split('.')[-1] if '.' in actual_filename else 'file'
                                name_without_ext = actual_filename.rsplit('.', 1)[0]
                                safe_name = re.sub(r'[^a-zA-Z0-9\-_]', '_', name_without_ext).strip('_')
                                if len(safe_name) < 3: safe_name = f"attachment_{idx}"
                                safe_filename = f"{safe_name}.{ext}"
                            except:
                                safe_filename = f"attachment_{idx}.file"
                            
                            attachment_files.append({
                                'data': file_data,
                                'filename': actual_filename,
                                'safe_filename': safe_filename
                            })
                except Exception as e:
                    logger.error(f"[NOTIFIER] Error downloading {att.name}: {e}")

        # Download Image
        if notice.image_url:
            try:
                async with session.get(notice.image_url, headers={'Referer': notice.url}, timeout=aiohttp.ClientTimeout(total=10)) as img_resp:
                    if img_resp.status == 200:
                        image_data = await img_resp.read()
                        image_filename = notice.image_url.split('/')[-1] or 'image.png'
            except Exception:
                pass

        # Validate embed size
        if len(embed.get("description", "")) > 4000:
            embed["description"] = embed["description"][:3950] + "...\n\n(내용이 잘렸습니다)"

        # Update embed field with better formatting
        if attachment_files:
            file_count = len(attachment_files)
            more_text = f" (+{len(notice.attachments) - 10} more)" if len(notice.attachments) > 10 else ""
            
            # Create nicely formatted file list
            file_list_lines = [f"📎 **첨부파일 ({file_count}개{more_text})**", "━━━━━━━━━━━━━━━"]
            
            for idx, file_info in enumerate(attachment_files, 1):
                # Get file extension for emoji
                ext = file_info['filename'].split('.')[-1].lower() if '.' in file_info['filename'] else ''
                emoji = {
                    'pdf': '📕',
                    'doc': '📘', 'docx': '📘',
                    'xls': '📗', 'xlsx': '📗',
                    'ppt': '📙', 'pptx': '📙',
                    'zip': '📦', 'rar': '📦',
                    'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️'
                }.get(ext, '📄')
                
                # Add hyperlink to filename
                # Note: Discord doesn't support linking to attachment:// in fields, so we link to original URL
                file_list_lines.append(f"{emoji} [{file_info['filename']}]({notice.attachments[idx-1].url})")
            
            embed["fields"].append({
                "name": "\u200b",
                "value": "\n".join(file_list_lines),
                "inline": False
            })

        # Send to Discord using DEDICATED session
        async with aiohttp.ClientSession() as discord_session:
            payload_data = None
            is_multipart = False

            if image_data or attachment_files:
                is_multipart = True
                # Use MultipartWriter directly to control headers
                writer = MultipartWriter('form-data')
                
                # IMPORTANT: payload_json MUST be the first field
                json_payload = StringPayload(json.dumps({"embeds": [embed]}), content_type='application/json')
                json_payload.set_content_disposition('form-data', name='payload_json')
                writer.append_payload(json_payload)
                
                file_index = 0
                if image_data:
                    # Image usually doesn't need special encoding if filename is simple
                    img_payload = BytesPayload(image_data, content_type='image/png')
                    img_payload.set_content_disposition('form-data', name=f'file{file_index}', filename=image_filename)
                    writer.append_payload(img_payload)
                    
                    embed["image"] = {"url": f"attachment://{image_filename}"}
                    file_index += 1
                
                for file_info in attachment_files:
                    # Use BytesPayload and manually set header to avoid URL encoding
                    file_payload = BytesPayload(file_info['data'], content_type='application/octet-stream')
                    
                    # Manually construct Content-Disposition with raw UTF-8 filename
                    # Discord expects: filename="한글.pdf" (raw bytes/string), NOT filename*=utf-8''%ED...
                    filename_str = file_info['filename']
                    # Escape quotes in filename just in case
                    filename_str = filename_str.replace('"', '\\"')
                    
                    file_payload.headers['Content-Disposition'] = f'form-data; name="file{file_index}"; filename="{filename_str}"'
                    writer.append_payload(file_payload)
                    
                    file_index += 1
                
                payload_data = writer
            else:
                payload_data = {"embeds": [embed]}

            # Retry logic
            for attempt in range(1, max_retries + 1):
                try:
                    if is_multipart:
                        async with discord_session.post(url, headers=headers, data=payload_data) as resp:
                            if resp.status in [200, 204]:
                                logger.info(f"[NOTIFIER] Discord sent: {notice.title}")
                                return
                            else:
                                logger.error(f"[NOTIFIER] Discord failed: {resp.status} - {await resp.text()}")
                    else:
                        async with discord_session.post(url, headers=headers, json=payload_data) as resp:
                            if resp.status in [200, 204]:
                                logger.info(f"[NOTIFIER] Discord sent: {notice.title}")
                                return
                            else:
                                logger.error(f"[NOTIFIER] Discord failed: {resp.status} - {await resp.text()}")
                                
                    if attempt < max_retries: await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"[NOTIFIER] Discord error: {e}")
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
