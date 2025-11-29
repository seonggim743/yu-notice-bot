import aiohttp
import json
import asyncio
import html
import urllib.parse
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
            "생활관": "🏠",
            "일반": "📢"
        }
        cat_emoji = cat_emojis.get(notice.category, "📢")
        
        # Status Prefix
        prefix = "🆕" if is_new else "🔄"
        
        # Content Construction
        safe_title = html.escape(notice.title)
        safe_summary = html.escape(notice.summary)
        
        # Site Name Mapping (Localization)
        site_name_map = {
            "yu_news": "영대소식",
            "cse_notice": "컴공공지",
            "bachelor_guide": "학사안내",
            "calendar": "학사일정",
            "dormitory_notice": "생활관공지",
            "dormitory_menu": "기숙사식단"
        }
        site_name = site_name_map.get(notice.site_key, notice.site_key)

        # Hashtags Mapping
        category_map = {
            "장학": "#Scholarship #장학",
            "학사": "#Academic #학사",
            "취업": "#Job #취업",
            "생활관": "#Dormitory #생활관",
            "일반": "#General #일반"
        }
        # Use localized site name in hashtag if category is generic
        if notice.category == "일반":
            hashtag = f"#{site_name}"
        else:
            hashtag = category_map.get(notice.category, f"#General #{notice.category}")
            # Append site name for context
            hashtag += f" #{site_name}"
        
        # Enhanced Message Format
        msg = (
            f"{prefix} <a href='{notice.url}'><b>{cat_emoji} {safe_title}</b></a>\n\n"
            f"{safe_summary}\n\n"
        )
        
        if modified_reason:
            msg += f"⚠️ <b>수정 사항</b>: {modified_reason}\n\n"
            
        msg += f"{hashtag}"

        # Buttons (Download Links)
        buttons = []
        if notice.attachments:
            for att in notice.attachments:
                fname = att.name
                ext = fname.split('.')[-1].lower() if '.' in fname else ''
                emoji = {
                    'pdf': '📕',
                    'doc': '📘', 'docx': '📘',
                    'xls': '📗', 'xlsx': '📗',
                    'ppt': '📙', 'pptx': '📙',
                    'zip': '📦', 'rar': '📦',
                    'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️'
                }.get(ext, '📄')
                
                if len(fname) > 20: fname = fname[:17] + "..."
                buttons.append({"text": f"{emoji} {fname}", "url": att.url})
        
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
        
        # 1. Download files first (needed for decision making)
        downloaded_files = []
        if notice.attachments:
            logger.info(f"[NOTIFIER] Downloading {len(notice.attachments)} attachments...")
            for idx, att in enumerate(notice.attachments, 1):
                # ... (Download logic same as before, simplified for brevity in this tool call) ...
                # We need to copy the robust download logic here.
                # To avoid code duplication and huge tool calls, I will implement a helper method for downloading later.
                # For now, I will inline the download logic but keep it concise.
                
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    try:
                        headers = {
                            'Referer': notice.url,
                            'User-Agent': settings.USER_AGENT,
                            'Accept': '*/*',
                            'Connection': 'keep-alive'
                        }
                        async with session.get(att.url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                file_data = await resp.read()
                                if len(file_data) > 50 * 1024 * 1024: break # Skip > 50MB
                                
                                actual_filename = att.name
                                if 'Content-Disposition' in resp.headers:
                                    import re
                                    from urllib.parse import unquote
                                    match = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)', resp.headers['Content-Disposition'])
                                    if match: actual_filename = unquote(match.group(1))
                                
                                downloaded_files.append({
                                    'data': file_data,
                                    'filename': actual_filename,
                                    'original_name': att.name
                                })
                                break
                            elif resp.status in [404, 403]: break
                            else:
                                if attempt < max_retries: await asyncio.sleep(1)
                    except Exception:
                        if attempt < max_retries: await asyncio.sleep(1)

        main_msg_id = None
        
        # 2. Decide Send Mode
        # Case A: Single File + Short Caption -> Send as Document with Caption
        if len(downloaded_files) == 1 and len(msg) <= 1024 and not notice.image_url:
            file_info = downloaded_files[0]
            logger.info(f"[NOTIFIER] Sending single file with caption: {file_info['filename']}")
            
            form = aiohttp.FormData()
            form.add_field('document', file_info['data'], filename=file_info['filename'], content_type='application/octet-stream')
            form.add_field('caption', msg)
            form.add_field('parse_mode', 'HTML')
            form.add_field('chat_id', str(self.chat_id))
            if topic_id: form.add_field('message_thread_id', str(topic_id))
            if buttons: form.add_field('reply_markup', json.dumps({"inline_keyboard": inline_keyboard}))
            
            try:
                async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendDocument", data=form) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        main_msg_id = result.get('result', {}).get('message_id')
                        logger.info(f"[NOTIFIER] Telegram document sent: {notice.title}")
                        return main_msg_id # Done!
            except Exception as e:
                logger.error(f"[NOTIFIER] Single file send failed: {e}, falling back to split mode.")
        
        # Case B: Standard Split Mode (Message + MediaGroup)
        # (Used if >1 files, or caption too long, or image exists, or single file send failed)
        
        # 2.1 Send Main Message (Text or Photo)
        if notice.image_url:
             # ... (Existing Photo Logic) ...
             # Re-implementing photo logic briefly
             try:
                headers = {'Referer': notice.url, 'User-Agent': 'Mozilla/5.0'}
                async with session.get(notice.image_url, headers=headers) as resp:
                    if resp.status == 200:
                        photo_data = await resp.read()
                        caption_text = msg[:1020] + "..." if len(msg) > 1024 else msg
                        
                        form = aiohttp.FormData()
                        form.add_field('photo', photo_data, filename='image.jpg')
                        form.add_field('caption', caption_text)
                        form.add_field('parse_mode', 'HTML')
                        form.add_field('chat_id', str(self.chat_id))
                        if topic_id: form.add_field('message_thread_id', str(topic_id))
                        if buttons: form.add_field('reply_markup', json.dumps({"inline_keyboard": inline_keyboard}))
                        
                        async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto", data=form) as photo_resp:
                            if photo_resp.status == 200:
                                result = await photo_resp.json()
                                main_msg_id = result.get('result', {}).get('message_id')
             except Exception: pass

        if not main_msg_id:
            # Fallback to Text
            payload = {
                'chat_id': self.chat_id,
                'text': msg,
                'parse_mode': 'HTML',
                'disable_web_page_preview': 'true'
            }
            if topic_id: payload['message_thread_id'] = topic_id
            if buttons: payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})
            
            try:
                async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendMessage", json=payload) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        main_msg_id = result.get('result', {}).get('message_id')
            except Exception as e:
                logger.error(f"[NOTIFIER] Telegram text send failed: {e}")
                return None

        # 2.2 Send Remaining Files (if any)
        # If we already sent the single file in Case A, we returned early.
        # So here we only handle files if we are in Case B.
        if main_msg_id and downloaded_files:
            # ... (Existing MediaGroup Logic) ...
            # Re-using the downloaded_files list we prepared at the start
             if len(downloaded_files) > 10:
                logger.warning(f"[NOTIFIER] Too many files ({len(downloaded_files)}), splitting...")
            
             for batch_idx in range(0, len(downloaded_files), 10):
                batch = downloaded_files[batch_idx:batch_idx + 10]
                media = []
                form = aiohttp.FormData()
                
                for idx, file_info in enumerate(batch):
                    field_name = f"file{idx}"
                    form.add_field(field_name, file_info['data'], filename=file_info['filename'])
                    media.append({"type": "document", "media": f"attach://{field_name}"})
                
                form.add_field('chat_id', str(self.chat_id))
                form.add_field('media', json.dumps(media))
                form.add_field('reply_to_message_id', str(main_msg_id))
                if topic_id: form.add_field('message_thread_id', str(topic_id))
                
                try:
                    async with session.post(f"https://api.telegram.org/bot{self.telegram_token}/sendMediaGroup", data=form) as resp:
                        if resp.status != 200: logger.error(f"MediaGroup failed: {await resp.text()}")
                except Exception as e: logger.error(f"MediaGroup error: {e}")

        return main_msg_id


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
        # Site Name Mapping (Localization)
        site_name_map = {
            "yu_news": "영대소식",
            "cse_notice": "컴공공지",
            "bachelor_guide": "학사안내",
            "calendar": "학사일정",
            "dormitory_notice": "생활관공지",
            "dormitory_menu": "기숙사식단"
        }
        site_name = site_name_map.get(notice.site_key, notice.site_key)

        # Color & Title Prefix
        color = 0x00ff00 if is_new else 0xffa500 # Green for New, Orange for Modified
        title_prefix = "🆕" if is_new else "🔄"
        
        # Embed Construction
        embed = {
            "title": f"{title_prefix} {notice.title}",
            "url": notice.url,
            "description": notice.summary,
            "color": color,
            "author": {
                "name": "Yu Notice Bot",
                "icon_url": "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png"
            },
            "footer": {
                "text": f"Category: {notice.category} • {site_name}"
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
                            actual_filename = att.name
                            
                            # Debug: Check what filename we're using
                            logger.info(f"[NOTIFIER] Original filename: '{actual_filename}'")
                            
                            attachment_files.append({
                                'data': file_data,
                                'filename': actual_filename,
                                'safe_filename': actual_filename,  # Use original as safe_filename too
                                'url': att.url
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
            
            # Group 1: Embed + Image (Always sent)
            # Group 2: Attachments (Sent with Group 1 if count=1, else sent separately)
            
            has_attachments = len(attachment_files) > 0
            split_attachments = len(attachment_files) > 1
            
            # Step A: Prepare Main Payload (Embed + Image + Optional Single Attachment)
            writer = MultipartWriter('form-data')
            
            # Add Embed
            json_payload = StringPayload(json.dumps({"embeds": [embed]}), content_type='application/json')
            json_payload.set_content_disposition('form-data', name='payload_json')
            writer.append_payload(json_payload)
            
            file_index = 0
            
            # Add Image (if exists)
            if image_data:
                img_payload = BytesPayload(image_data, content_type='image/png')
                img_payload.set_content_disposition('form-data', name=f'file{file_index}', filename=image_filename)
                writer.append_payload(img_payload)
                embed["image"] = {"url": f"attachment://{image_filename}"} # Update embed to point to this file
                file_index += 1
            
            # Add Single Attachment (if NOT splitting)
            if has_attachments and not split_attachments:
                file_info = attachment_files[0]
                # ... (Add file logic) ...
                # Copying the RFC 5987 logic
                file_payload = BytesPayload(file_info['data'], content_type='application/octet-stream')
                filename_utf8 = file_info['filename']
                encoded_filename = urllib.parse.quote(filename_utf8)
                ext = filename_utf8.split('.')[-1] if '.' in filename_utf8 else 'file'
                fallback_filename = f"attachment_{file_index}.{ext}" # Use file_index for unique fallback
                file_payload.headers['Content-Disposition'] = (
                    f'form-data; name="file{file_index}"; '
                    f'filename="{fallback_filename}"; '
                    f'filename*=utf-8\'\'{encoded_filename}'
                )
                writer.append_payload(file_payload)
                file_index += 1

            # Send Main Message
            logger.info(f"[NOTIFIER] Sending Discord Main Message (Split={split_attachments})...")
            for attempt in range(1, max_retries + 1):
                try:
                    async with discord_session.post(url, headers=headers, data=writer) as resp:
                        if resp.status in [200, 204]:
                            logger.info(f"[NOTIFIER] Discord Main sent")
                            break
                        else:
                            logger.error(f"[NOTIFIER] Discord Main failed: {resp.status} - {await resp.text()}")
                            if attempt < max_retries: await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"[NOTIFIER] Discord Main error: {e}")
                    if attempt < max_retries: await asyncio.sleep(1)

            # Step B: Send Remaining Attachments (if splitting)
            if has_attachments and split_attachments:
                logger.info(f"[NOTIFIER] Sending {len(attachment_files)} separate attachments...")
                
                # Discord limit: 10 files per message. Batch them.
                for batch_idx in range(0, len(attachment_files), 10):
                    batch = attachment_files[batch_idx:batch_idx + 10]
                    logger.info(f"[NOTIFIER] Sending Discord attachment batch {batch_idx//10 + 1} ({len(batch)} files)")
                    
                    writer_atts = MultipartWriter('form-data')
                    file_index = 0
                    for idx, file_info in enumerate(batch, 1):
                        file_payload = BytesPayload(file_info['data'], content_type='application/octet-stream')
                        filename_utf8 = file_info['filename']
                        encoded_filename = urllib.parse.quote(filename_utf8)
                        ext = filename_utf8.split('.')[-1] if '.' in filename_utf8 else 'file'
                        fallback_filename = f"attachment_{batch_idx + idx}.{ext}"
                        file_payload.headers['Content-Disposition'] = (
                            f'form-data; name="file{file_index}"; '
                            f'filename="{fallback_filename}"; '
                            f'filename*=utf-8\'\'{encoded_filename}'
                        )
                        writer_atts.append_payload(file_payload)
                        file_index += 1
                    
                    for attempt in range(1, max_retries + 1):
                        try:
                            async with discord_session.post(url, headers=headers, data=writer_atts) as resp:
                                if resp.status in [200, 204]:
                                    logger.info(f"[NOTIFIER] Discord Attachments batch sent")
                                    break
                                else:
                                    logger.error(f"[NOTIFIER] Discord Attachments batch failed: {resp.status} - {await resp.text()}")
                                    if attempt < max_retries: await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"[NOTIFIER] Discord Attachments batch error: {e}")
                            if attempt < max_retries: await asyncio.sleep(1)

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
