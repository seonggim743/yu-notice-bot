import aiohttp
import json
import asyncio
import html
import urllib.parse
from aiohttp import MultipartWriter
from aiohttp.payload import BytesPayload, StringPayload
from datetime import datetime
from typing import List, Dict, Optional, Any
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

        # Hashtags
        hashtag = f"#{notice.category} #{site_name}"
        
        # Enhanced Message Format
        # Ensure every line starts with a hyphen
        lines = safe_summary.split('\n')
        formatted_lines = []
        for line in lines:
            line = line.strip()
            if not line: continue
            if not line.startswith("-"):
                line = f"- {line}"
            formatted_lines.append(line)
        formatted_summary = "\n".join(formatted_lines)

        msg = (
            f"{prefix} <a href='{notice.url}'><b>{cat_emoji} {safe_title}</b></a>\n\n"
            f"📝 <b>요약</b>\n"
            f"{formatted_summary}\n\n"
        )
        
        # Tier 2: Deadline & Eligibility
        if notice.deadline:
            msg += f"📅 <b>마감일</b>: {notice.deadline}\n"
            
        if notice.eligibility:
            # Limit to 3 items to keep it clean
            items = notice.eligibility[:3]
            reqs = "\n".join([f"• {html.escape(req)}" for req in items])
            msg += f"✅ <b>자격요건</b>\n{reqs}\n\n"
        elif notice.deadline: # Add newline if only deadline exists
            msg += "\n"
        
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
        # Check for Image URL OR Preview Image
        if notice.image_url or notice.preview_image:
             try:
                photo_data = None
                
                if notice.image_url:
                    headers = {'Referer': notice.url, 'User-Agent': 'Mozilla/5.0'}
                    async with session.get(notice.image_url, headers=headers) as resp:
                        if resp.status == 200:
                            photo_data = await resp.read()
                elif notice.preview_image:
                    photo_data = notice.preview_image
                    logger.info(f"[NOTIFIER] Using PDF preview image for Telegram ({len(photo_data)} bytes)")

                if photo_data:
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
                        else:
                            logger.error(f"[NOTIFIER] Telegram photo send failed: {await photo_resp.text()}")
             except Exception as e:
                 logger.error(f"[NOTIFIER] Telegram photo error: {e}")

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


    async def send_discord(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str = "", existing_thread_id: str = None) -> Optional[str]:
        """
        Sends a notice to Discord (Forum Channel preferred).
        Returns the Thread ID (or Message ID) if successful, None otherwise.
        """
        bot_token = settings.DISCORD_BOT_TOKEN
        channel_map = settings.DISCORD_CHANNEL_MAP
        
        if not bot_token or not channel_map: return None
        
        channel_id = channel_map.get(notice.site_key)
        
        if channel_id:
            # 1. Try sending as a Forum Thread
            thread_url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
            message_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            
            headers = {
                "Authorization": f"Bot {bot_token}",
                "User-Agent": "DiscordBot (https://github.com/yu-notice-bot, v1.0)"
            }
            
            return await self._send_discord_common(session, notice, is_new, modified_reason, thread_url, message_url, headers, existing_thread_id=existing_thread_id)
        else:
            logger.warning(f"[NOTIFIER] No Discord channel found for key '{notice.site_key}'")
            return None

    async def _send_discord_common(self, session: aiohttp.ClientSession, notice: Notice, is_new: bool, modified_reason: str, thread_url: str, message_url: str, headers: Dict, max_retries: int = 3, existing_thread_id: str = None) -> Optional[str]:
        """
        Common method to send Discord notifications.
        Tries to create a Forum Thread first, falls back to Message.
        If existing_thread_id is provided for a modified notice, it sends a reply.
        Returns the ID of the created thread/message, or existing_thread_id if updated, None otherwise.
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
        
        # Thread Name (Title)
        thread_name = f"[{notice.category}] {notice.title}"
        if len(thread_name) > 100: thread_name = thread_name[:97] + "..."
        
        # ... (Rest of embed construction remains similar) ...
        
        # Ensure every line starts with a hyphen
        lines = notice.summary.split('\n')
        formatted_lines = []
        for line in lines:
            line = line.strip()
            if not line: continue
            if not line.startswith("-"):
                line = f"- {line}"
            formatted_lines.append(line)
        formatted_summary = "\n".join(formatted_lines)
        
        # Embed Construction
        embed = {
            "title": f"{title_prefix} {notice.title}",
            "url": notice.url,
            "description": f"📝 **요약**\n{formatted_summary}",
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
        
        # Tier 2: Deadline & Eligibility
        if notice.deadline:
            embed["fields"].append({
                "name": "📅 마감일",
                "value": notice.deadline,
                "inline": True
            })
            
        if notice.eligibility:
            items = notice.eligibility[:3]
            reqs = "\n".join([f"• {req}" for req in items])
            embed["fields"].append({
                "name": "✅ 자격요건",
                "value": reqs,
                "inline": False
            })

        if modified_reason:
            embed["fields"].append({
                "name": "⚠️ 수정 사항",
                "value": modified_reason,
                "inline": False
            })
        
        # Add attachment links as the last field (before footer)
        if notice.attachments:
            attachment_links = ""
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
                attachment_links += f"{emoji} [{fname}]({att.url})\n"
            
            embed["fields"].append({
                "name": "📎 첨부파일",
                "value": attachment_links.strip(),
                "inline": False
            })
             
        # Download attachments using the SHARED session (to handle hotlink protection/cookies)
        attachment_files = []
        image_data = None
        image_filename = "image.png"

        # 1. Handle Main Image (Priority: URL > Preview Bytes)
        if notice.image_url:
            try:
                async with session.get(notice.image_url, headers={'Referer': notice.url}, timeout=aiohttp.ClientTimeout(total=10)) as img_resp:
                    if img_resp.status == 200:
                        image_data = await img_resp.read()
                        image_filename = "image.jpg"
                        embed["image"] = {"url": f"attachment://{image_filename}"}
            except Exception as e:
                logger.error(f"[NOTIFIER] Failed to download image {notice.image_url}: {e}")

        if not image_data and notice.preview_image:
            image_data = notice.preview_image
            image_filename = "preview.jpg"
            embed["image"] = {"url": f"attachment://{image_filename}"}

        # 2. Handle Attachments
        if notice.attachments:
            for idx, att in enumerate(notice.attachments[:10], 1):
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    try:
                        download_headers = {
                            'Referer': notice.url,
                            'User-Agent': settings.USER_AGENT,
                            'Accept': '*/*',
                            'Connection': 'keep-alive'
                        }
                        # Use shared session for download
                        async with session.get(att.url, headers=download_headers, timeout=aiohttp.ClientTimeout(total=30)) as file_resp:
                            if file_resp.status == 200:
                                file_data = await file_resp.read()
                                file_size = len(file_data)
                                if file_size > 25 * 1024 * 1024: break # Skip > 25MB
                                
                                actual_filename = att.name
                                # Try to get filename from Content-Disposition if available
                                if 'Content-Disposition' in file_resp.headers:
                                    import re
                                    from urllib.parse import unquote
                                    match = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)', file_resp.headers['Content-Disposition'])
                                    if match: actual_filename = unquote(match.group(1))
                                
                                logger.info(f"[NOTIFIER] Downloaded attachment: '{actual_filename}' ({file_size} bytes)")
                                
                                attachment_files.append({
                                    'data': file_data,
                                    'filename': actual_filename,
                                    'safe_filename': actual_filename,
                                    'url': att.url
                                })
                                break # Success, exit retry loop
                            elif file_resp.status in [404, 403]:
                                logger.warning(f"[NOTIFIER] Failed to download {att.name}: Status {file_resp.status}")
                                break # Don't retry for 404/403
                            else:
                                if attempt < max_retries: await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"[NOTIFIER] Error downloading {att.name}: {e}")
                        if attempt < max_retries: await asyncio.sleep(1)

        # Logic for Splitting Attachments
        # Rule: 
        # - 1 Attachment: Send with Main Message
        # - 2+ Attachments: Send via Reply (Thread/Message)
        
        files_to_send_now = []
        files_to_send_later = []
        
        if len(attachment_files) == 1:
            files_to_send_now = attachment_files
        elif len(attachment_files) >= 2:
            files_to_send_later = attachment_files
            
        logger.info(f"[NOTIFIER] Attachments: {len(attachment_files)} | Now: {len(files_to_send_now)} | Later: {len(files_to_send_later)}")
        logger.info(f"[NOTIFIER] Has Image: {bool(image_data)}")
            
        # Prepare Payload
        # We need to construct the payload differently for Thread vs Message
        
        # 0. Handle Update Reply (if existing_thread_id)
        if not is_new and existing_thread_id:
            logger.info(f"[NOTIFIER] Sending update reply to existing thread: {existing_thread_id}")
            
            # Construct Update Embed (Override the default one)
            update_embed = {
                "title": "⚠️ 공지사항 수정 알림",
                "description": f"**수정 사유:** {modified_reason}\n\n[원본 공지 보러가기]({notice.url})",
                "color": 0xFFA500, # Orange
                "footer": {"text": "Yu Notice Bot • 업데이트됨"},
                "timestamp": datetime.utcnow().isoformat()
            }
            if notice.summary:
                update_embed["fields"] = [{"name": "📝 요약 (업데이트)", "value": notice.summary[:1000], "inline": False}]

            # Prepare Payload
            payload = {"embeds": [update_embed]}
            
            # Determine if we need Multipart (Files) or JSON
            has_files_now = bool(image_data or files_to_send_now)
            
            if has_files_now:
                form = aiohttp.FormData()
                form.add_field('payload_json', json.dumps(payload))
                
                if image_data:
                    filename = 'image.jpg' if notice.image_url else 'preview.jpg'
                    form.add_field('files[0]', image_data, filename=filename)
                    
                for idx, file_info in enumerate(files_to_send_now):
                    field_name = f"files[{idx + 1}]" if image_data else f"files[{idx}]"
                    form.add_field(field_name, file_info['data'], filename=file_info['filename'])
                    
                kwargs = {'data': form}
            else:
                kwargs = {'json': payload}

            # Send Reply
            reply_url = f"https://discord.com/api/v10/channels/{existing_thread_id}/messages"
            
            try:
                async with session.post(reply_url, headers=headers, **kwargs) as resp:
                    if resp.status in [200, 201]:
                        logger.info(f"[NOTIFIER] Discord update reply sent.")
                        
                        # Send remaining files if any
                        if files_to_send_later:
                            await self._send_discord_reply(session, existing_thread_id, files_to_send_later, headers, is_thread=True)
                            
                        return existing_thread_id
                    elif resp.status == 404:
                         logger.warning(f"[NOTIFIER] Thread {existing_thread_id} not found. Creating new thread.")
                         # Fall through to create new thread
                    else:
                        logger.error(f"[NOTIFIER] Failed to send update reply: {await resp.text()}")
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending update reply: {e}")

        created_thread_id = None
        created_message_id = None
        
        # 1. Try Thread Creation (Forum)
        try:
            # Forum Thread Payload
            payload = {
                "name": thread_name,
                "message": {
                    "embeds": [embed]
                },
                "auto_archive_duration": 4320 # 3 days
            }
            
            # Determine if we need Multipart (Files) or JSON
            has_files_now = bool(image_data or files_to_send_now)
            
            if has_files_now:
                form = aiohttp.FormData()
                form.add_field('payload_json', json.dumps(payload))
                
                # Add Files (Main Image + Attachments)
                if image_data:
                    filename = 'image.jpg' if notice.image_url else 'preview.jpg'
                    form.add_field('files[0]', image_data, filename=filename)
                    
                for idx, file_info in enumerate(files_to_send_now):
                    field_name = f"files[{idx + 1}]" if image_data else f"files[{idx}]"
                    form.add_field(field_name, file_info['data'], filename=file_info['filename'])
                    
                kwargs = {'data': form}
            else:
                kwargs = {'json': payload}
            
            async with session.post(thread_url, headers=headers, **kwargs) as resp:
                if resp.status in [200, 201]:
                    logger.info(f"[NOTIFIER] Discord Forum Thread created: {thread_name}")
                    resp_data = await resp.json()
                    created_thread_id = resp_data.get('id')
                    
                    # If we have files to send later, send them to the thread
                    if files_to_send_later and created_thread_id:
                        await self._send_discord_reply(session, created_thread_id, files_to_send_later, headers, is_thread=True)
                        
                    return created_thread_id
                elif resp.status == 400 or resp.status == 404:
                    logger.warning(f"[NOTIFIER] Failed to create thread (Status {resp.status}). Fallback to normal message.")
                else:
                    logger.error(f"[NOTIFIER] Discord Thread creation failed: {await resp.text()}")
                    pass

        except Exception as e:
            logger.error(f"[NOTIFIER] Discord Thread error: {e}")

        # 2. Fallback: Normal Message (Text Channel)
        try:
            payload = {"embeds": [embed]}
            
            has_files_now = bool(image_data or files_to_send_now)
            
            if has_files_now:
                form = aiohttp.FormData()
                form.add_field('payload_json', json.dumps(payload))
                
                if image_data:
                    filename = 'image.jpg' if notice.image_url else 'preview.jpg'
                    form.add_field('files[0]', image_data, filename=filename)
                    
                for idx, file_info in enumerate(files_to_send_now):
                    field_name = f"files[{idx + 1}]" if image_data else f"files[{idx}]"
                    form.add_field(field_name, file_info['data'], filename=file_info['filename'])
                    
                kwargs = {'data': form}
            else:
                kwargs = {'json': payload}
                
            async with session.post(message_url, headers=headers, **kwargs) as resp:
                if resp.status in [200, 204]:
                    logger.info(f"[NOTIFIER] Discord Message sent: {notice.title}")
                    resp_data = await resp.json()
                    created_message_id = resp_data.get('id')
                    channel_id = message_url.split('/')[-2] # Extract channel ID from URL
                    
                    # If we have files to send later, reply to the message
                    if files_to_send_later and created_message_id:
                        await self._send_discord_reply(session, channel_id, files_to_send_later, headers, is_thread=False, reply_to_id=created_message_id)
                        
                    return created_message_id
                else:
                    logger.error(f"[NOTIFIER] Discord Message failed: {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"[NOTIFIER] Discord Message error: {e}")
            return None

    async def _send_discord_reply(self, session: aiohttp.ClientSession, channel_id: str, files: List[Dict], headers: Dict, is_thread: bool, reply_to_id: str = None):
        """
        Sends a reply (follow-up message) with attachments.
        """
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        
        # Batch files (max 10 per message)
        for batch_idx in range(0, len(files), 10):
            batch = files[batch_idx:batch_idx + 10]
            
            form = aiohttp.FormData()
            payload = {}
            if reply_to_id and not is_thread:
                payload["message_reference"] = {"message_id": reply_to_id}
            
            form.add_field('payload_json', json.dumps(payload))
            
            for idx, file_info in enumerate(batch):
                field_name = f"files[{idx}]"
                form.add_field(field_name, file_info['data'], filename=file_info['filename'])
                
            try:
                async with session.post(url, headers=headers, data=form) as resp:
                    if resp.status not in [200, 201, 204]:
                        logger.error(f"[NOTIFIER] Failed to send reply attachments: {await resp.text()}")
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending reply attachments: {e}")

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
