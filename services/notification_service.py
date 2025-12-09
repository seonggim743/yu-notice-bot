import aiohttp
import json
import asyncio
import html
from datetime import datetime
from typing import List, Dict, Optional, Any
from core.config import settings
from core.logger import get_logger
from models.notice import Notice
from services.tag_matcher import TagMatcher
from services.notification.formatters import (
    create_telegram_message,
    create_telegram_message,
    generate_clean_diff,
)
import urllib.parse
from aiohttp import MultipartWriter
from core import constants

logger = get_logger(__name__)


class NotificationService:
    def __init__(self):
        self.telegram_token = settings.TELEGRAM_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    def _add_text_part(self, writer: MultipartWriter, name: str, value: Any) -> None:
        """Adds a text field to MultipartWriter."""
        part = writer.append(str(value))
        part.set_content_disposition("form-data", name=name)

    def _add_file_part(
        self,
        writer: MultipartWriter,
        field_name: str,
        file_data: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        """
        Adds a file to MultipartWriter with manual Content-Disposition header.
        Supports both raw UTF-8 (Discord/Legacy) and RFC 5987 (Telegram/Standard).
        """
        # 1. Append payload
        part = writer.append(file_data, {"Content-Type": content_type})

        # 2. Prepare filenames
        # RFC 5987: Percent-encoded
        filename_star = urllib.parse.quote(filename)
        # Legacy: Raw UTF-8 (escape quotes)
        filename_legacy = filename.replace('"', '\\"')

        # 3. Construct new header value
        new_header_value = (
            f'form-data; name="{field_name}"; '
            f'filename="{filename_legacy}"; '
            f"filename*=UTF-8''{filename_star}"
        )

        # 4. Set Header
        part.headers["Content-Disposition"] = new_header_value

    async def _send_telegram_api(
        self,
        session: aiohttp.ClientSession,
        method: str,
        payload: dict = None,
        data: Any = None,
        retries: int = 3,
    ) -> Optional[Dict]:
        """
        Helper to send Telegram API requests with rate limit handling (429).
        """
        url = f"https://api.telegram.org/bot{self.telegram_token}/{method}"
        
        for attempt in range(retries):
            try:
                # Use data for FormData/MultipartWriter, json for simple payloads
                if data:
                    async with session.post(url, data=data) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        elif resp.status == 429:
                            resp_json = await resp.json()
                            retry_after = resp_json.get("parameters", {}).get("retry_after", 5)
                            logger.warning(
                                f"[NOTIFIER] Telegram 429 (Too Many Requests). Waiting {retry_after}s..."
                            )
                            await asyncio.sleep(retry_after + 1)
                            continue
                        else:
                            logger.error(
                                f"[NOTIFIER] Telegram API {method} failed (Status {resp.status}): {await resp.text()}"
                            )
                            return None
                else:
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        elif resp.status == 429:
                            resp_json = await resp.json()
                            retry_after = resp_json.get("parameters", {}).get("retry_after", 5)
                            logger.warning(
                                f"[NOTIFIER] Telegram 429 (Too Many Requests). Waiting {retry_after}s..."
                            )
                            await asyncio.sleep(retry_after + 1)
                            continue
                        else:
                            logger.error(
                                f"[NOTIFIER] Telegram API {method} failed (Status {resp.status}): {await resp.text()}"
                            )
                            return None
            except Exception as e:
                logger.error(f"[NOTIFIER] Telegram API request error: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2)
                else:
                    return None
        return None

    def generate_clean_diff(self, old_text: str, new_text: str) -> str:
        """
        Generates a clean, line-by-line diff showing only changes.
        Delegates to formatters module.
        """
        return generate_clean_diff(old_text, new_text)

    async def send_telegram(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[int] = None,
        changes: Optional[Dict] = None,
    ) -> Optional[int]:
        """
        Sends a notice to Telegram with enhanced formatting. Returns the Message ID.
        """
        if not self.telegram_token:
            return None

        # Source-based Routing
        topic_id = settings.TELEGRAM_TOPIC_MAP.get(notice.site_key)

        # Create message using formatter
        msg = create_telegram_message(notice, is_new, modified_reason, changes)

        # Buttons (Download Links)
        buttons = []
        if notice.attachments:
            for att in notice.attachments:
                fname = att.name
                ext = fname.split(".")[-1].lower() if "." in fname else ""
                emoji = constants.FILE_EMOJI_MAP.get(ext, constants.FILE_EMOJI_MAP["default"])

                if len(fname) > constants.FILENAME_TRUNCATE_LENGTH:
                    fname = fname[: constants.FILENAME_TRUNCATE_LENGTH - 3] + "..."
                buttons.append({"text": f"{emoji} {fname}", "url": att.url})

        main_msg_id = None

        # Prepare inline keyboard for buttons (if any)
        inline_keyboard = (
            [[{"text": b["text"], "url": b["url"]}] for b in buttons]
            if buttons
            else None
        )

        # Separate lists for Telegram
        content_images_to_send = []
        pdf_previews_to_send = []

        # A. Content Images (Multiple images support)
        # Fix: Only send content images if it's a new post OR images actually changed.
        should_send_content_images = is_new or (changes and "image" in changes)
        
        if notice.image_urls and should_send_content_images:
            for idx, image_url in enumerate(notice.image_urls):
                try:
                    headers = {"Referer": notice.url, "User-Agent": settings.USER_AGENT}
                    async with session.get(image_url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            # Only first image gets the main caption
                            caption = msg if idx == 0 else None
                            content_images_to_send.append(
                                {
                                    "type": "content",
                                    "data": data,
                                    "filename": f"image_{idx}.jpg",
                                    "caption": caption,
                                }
                            )
                            logger.info(
                                f"[NOTIFIER] Added content image {idx + 1}/{len(notice.image_urls)}"
                            )
                except Exception as e:
                    logger.error(
                        f"[NOTIFIER] Failed to download content image {idx}: {e}"
                    )

        # B. PDF Previews (All previews as separate images)
        # Check attachments for preview_images
        if notice.attachments:
            for att in notice.attachments:
                if getattr(att, "preview_images", None):
                    for idx, img_data in enumerate(att.preview_images):
                        # PDF previews always show as [미리보기] only
                        # Only show filename on the first page
                        caption = f"📑 [미리보기] {att.name}" if idx == 0 else ""

                        pdf_previews_to_send.append(
                            {
                                "type": "preview",
                                "data": img_data,
                                "filename": f"preview_{att.name}_p{idx + 1}.jpg",
                                "caption": caption,
                            }
                        )
                        logger.info(
                            f"[NOTIFIER] Added PDF preview page {idx + 1} for {att.name}"
                        )

        # C. Send Logic
        # If we only have PDF previews (no content images), send text message first
        if pdf_previews_to_send and not content_images_to_send:
            # Send text message first for PDF-only notices
            payload = {
                "chat_id": self.chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            if topic_id:
                payload["message_thread_id"] = topic_id
            if buttons:
                payload["reply_markup"] = json.dumps(
                    {"inline_keyboard": inline_keyboard}
                )

            # If updating, reply to existing message
            if not is_new and existing_message_id:
                payload["reply_to_message_id"] = existing_message_id

            result = await self._send_telegram_api(session, "sendMessage", payload=payload)
            if result:
                main_msg_id = result.get("result", {}).get("message_id")
                logger.info(
                    "[NOTIFIER] Sent text message first for PDF-only notice"
                )

        if not content_images_to_send and not pdf_previews_to_send:
            # Text Only
            payload = {
                "chat_id": self.chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            if topic_id:
                payload["message_thread_id"] = topic_id
            if buttons:
                payload["reply_markup"] = json.dumps(
                    {"inline_keyboard": inline_keyboard}
                )

            # If updating, reply to existing message
            if not is_new and existing_message_id:
                payload["reply_to_message_id"] = existing_message_id

            result = await self._send_telegram_api(session, "sendMessage", payload=payload)
            if result:
                main_msg_id = result.get("result", {}).get("message_id")

        elif len(content_images_to_send) == 1 and not pdf_previews_to_send:
            # Single Photo (Content only)
            img = content_images_to_send[0]
            form = MultipartWriter("form-data")
            self._add_file_part(form, "photo", img["data"], img["filename"])
            self._add_text_part(
                form, "caption", img["caption"][: constants.DISCORD_MAX_EMBED_LENGTH]
            )  # Caption limit
            self._add_text_part(form, "parse_mode", "HTML")
            self._add_text_part(form, "chat_id", str(self.chat_id))
            if topic_id:
                self._add_text_part(form, "message_thread_id", str(topic_id))
            if buttons:
                self._add_text_part(
                    form, "reply_markup", json.dumps({"inline_keyboard": inline_keyboard})
                )

            # If updating, reply to existing message
            if not is_new and existing_message_id:
                self._add_text_part(form, "reply_to_message_id", str(existing_message_id))

            result = await self._send_telegram_api(session, "sendPhoto", data=form)
            if result:
                main_msg_id = result.get("result", {}).get("message_id")

        else:
            # Multiple Photos or Mix of Content + Previews
            # Send content images (if any)
            if content_images_to_send:
                # Case A: Single Content Image -> Use sendPhoto (MediaGroup requires 2+)
                if len(content_images_to_send) == 1:
                    img = content_images_to_send[0]
                    form = aiohttp.FormData()
                    self._add_file_to_form(form, "photo", img["data"], img["filename"])
                    if img.get("caption"):
                        form.add_field("caption", img["caption"][: constants.DISCORD_MAX_EMBED_LENGTH])
                        form.add_field("parse_mode", "HTML")
                    form.add_field("chat_id", str(self.chat_id))
                    if topic_id:
                        form.add_field("message_thread_id", str(topic_id))
                    
                    # If updating, reply to existing message
                    if not is_new and existing_message_id:
                        form.add_field("reply_to_message_id", str(existing_message_id))

                    result = await self._send_telegram_api(session, "sendPhoto", data=form)
                    if result:
                        main_msg_id = result.get("result", {}).get("message_id")
                        logger.info("[NOTIFIER] Sent single content image via sendPhoto (Mixed Mode)")

                # Case B: Multiple Content Images -> Use sendMediaGroup
                else:
                    media = []
                    form = MultipartWriter("form-data")

                    for idx, img in enumerate(content_images_to_send):
                        field_name = f"file{idx}"
                        self._add_file_part(form, field_name, img["data"], img["filename"])

                        media_item = {"type": "photo", "media": f"attach://{field_name}"}
                        if idx == 0 and img.get("caption"):
                            media_item["caption"] = img["caption"][: constants.DISCORD_MAX_EMBED_LENGTH]
                            media_item["parse_mode"] = "HTML"

                        media.append(media_item)

                    self._add_text_part(form, "chat_id", str(self.chat_id))
                    self._add_text_part(form, "media", json.dumps(media))
                    if topic_id:
                        self._add_text_part(form, "message_thread_id", str(topic_id))

                    # If updating, reply to existing message
                    if not is_new and existing_message_id:
                        self._add_text_part(form, "reply_to_message_id", str(existing_message_id))

                    result = await self._send_telegram_api(session, "sendMediaGroup", data=form)
                    if result:
                        main_msg_id = result.get("result", [{}])[0].get(
                            "message_id"
                        )
                        logger.info(
                            f"[NOTIFIER] Sent {len(content_images_to_send)} content images as MediaGroup"
                        )

            # Send PDF previews as replies to main message (Grouped by PDF)
            if main_msg_id and pdf_previews_to_send:
                # Group previews by filename (original attachment name)
                # Re-iterate attachments to get grouped images directly
                if notice.attachments:
                    for att in notice.attachments:
                        if getattr(att, "preview_images", None):
                            # Split into chunks of 10 (Telegram limit)
                            preview_chunks = [
                                att.preview_images[i : i + 10]
                                for i in range(0, len(att.preview_images), 10)
                            ]

                            for chunk_idx, chunk in enumerate(preview_chunks):
                                media = []
                                form = MultipartWriter("form-data")

                                for idx, img_data in enumerate(chunk):
                                    # Global index for filename
                                    global_idx = (chunk_idx * 10) + idx
                                    field_name = f"pdf_{chunk_idx}_{idx}"
                                    self._add_file_part(
                                        form,
                                        field_name,
                                        img_data,
                                        f"preview_{att.name}_p{global_idx + 1}.jpg",
                                    )

                                    media_item = {
                                        "type": "photo",
                                        "media": f"attach://{field_name}",
                                    }
                                    # Caption only on the very first image of the first chunk
                                    if chunk_idx == 0 and idx == 0:
                                        media_item["caption"] = f"📑 [미리보기] {att.name}"
                                        media_item["parse_mode"] = "HTML"
                                    media.append(media_item)

                                if media:
                                    self._add_text_part(form, "chat_id", str(self.chat_id))
                                    self._add_text_part(form, "media", json.dumps(media))
                                    self._add_text_part(form, "reply_to_message_id", str(main_msg_id))
                                    if topic_id:
                                        self._add_text_part(form, "message_thread_id", str(topic_id))

                                    result = await self._send_telegram_api(session, "sendMediaGroup", data=form)
                                    if result:
                                        logger.info(
                                            f"[NOTIFIER] Sent PDF preview chunk {chunk_idx + 1} for {att.name}"
                                        )
                                    
                                    # Small delay between chunks to prevent rate limiting (even with retries)
                                    await asyncio.sleep(1.0)

        # 2.2 Send Attachments as MediaGroup (All Together)
        if main_msg_id and notice.attachments:
            collected_files = []

            # Download all files first
            for idx, att in enumerate(notice.attachments[:10], 1):
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    try:
                        download_headers = {
                            "Referer": notice.url,
                            "User-Agent": settings.USER_AGENT,
                            "Accept": "*/*",
                            "Connection": "keep-alive",
                        }
                        async with session.get(
                            att.url,
                            headers=download_headers,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as file_resp:
                            if file_resp.status == 200:
                                file_data = await file_resp.read()
                                file_size = len(file_data)
                                if file_size > constants.TELEGRAM_FILE_SIZE_LIMIT:
                                    logger.warning(
                                        f"[NOTIFIER] File {att.name} too large ({file_size} bytes), skipping"
                                    )
                                    break

                                actual_filename = att.name
                                if "Content-Disposition" in file_resp.headers:
                                    import re
                                    from urllib.parse import unquote

                                    match = re.search(
                                        r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)',
                                        file_resp.headers["Content-Disposition"],
                                    )
                                    if match:
                                        actual_filename = unquote(match.group(1))

                                collected_files.append((actual_filename, file_data))
                                logger.info(
                                    f"[NOTIFIER] Downloaded file {idx}/{len(notice.attachments)}: {actual_filename}"
                                )
                                break
                            elif file_resp.status in [404, 403]:
                                logger.warning(
                                    f"[NOTIFIER] Failed to download {att.name}: Status {file_resp.status}"
                                )
                                break
                            else:
                                if attempt < max_retries:
                                    await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"[NOTIFIER] Error downloading {att.name}: {e}")
                        if attempt < max_retries:
                            await asyncio.sleep(1)

            # Send all files as MediaGroup
            if collected_files:
                media = []
                form = MultipartWriter("form-data")

                for idx, (filename, filedata) in enumerate(collected_files):
                    field_name = f"doc{idx}"
                    self._add_file_part(form, field_name, filedata, filename)
                    media.append(
                        {"type": "document", "media": f"attach://{field_name}"}
                    )

                self._add_text_part(form, "media", json.dumps(media))
                self._add_text_part(form, "chat_id", str(self.chat_id))
                if topic_id:
                    self._add_text_part(form, "message_thread_id", str(topic_id))
                self._add_text_part(form, "reply_to_message_id", str(main_msg_id))

                result = await self._send_telegram_api(session, "sendMediaGroup", data=form)
                if result:
                    logger.info(
                        f"[NOTIFIER] Sent {len(collected_files)} files as MediaGroup"
                    )

        # 2.3 Send Detailed Change Content (if modified)
        if main_msg_id and modified_reason and notice.change_details:
            old_content = notice.change_details.get("old_content")
            new_content = notice.change_details.get("new_content")

            if old_content and new_content:
                if old_content and new_content:
                    diff_text = self.generate_clean_diff(old_content, new_content)

                    if diff_text:
                        # Split if too long for Telegram (Limit ~4096)
                        # We use 4000 to be safe with headers
                        if len(diff_text) > constants.TELEGRAM_MAX_MESSAGE_LENGTH - 96:
                            chunks = [
                                diff_text[i : i + (constants.TELEGRAM_MAX_MESSAGE_LENGTH - 96)]
                                for i in range(0, len(diff_text), (constants.TELEGRAM_MAX_MESSAGE_LENGTH - 96))
                            ]
                            for idx, chunk in enumerate(chunks):
                                detail_msg = (
                                    f"🔍 <b>상세 변경 내용 ({idx + 1}/{len(chunks)})</b>\n"
                                    f"<pre>{html.escape(chunk)}</pre>"
                                )
                                reply_payload = {
                                    "chat_id": self.chat_id,
                                    "text": detail_msg,
                                    "reply_to_message_id": main_msg_id,
                                    "parse_mode": "HTML",
                                }
                                if topic_id:
                                    reply_payload["message_thread_id"] = topic_id

                                result = await self._send_telegram_api(session, "sendMessage", payload=reply_payload)
                                if result:
                                    await asyncio.sleep(0.2)
                        else:
                            detail_msg = (
                                f"🔍 <b>상세 변경 내용</b>\n"
                                f"<pre>{html.escape(diff_text)}</pre>"
                            )
                            reply_payload = {
                                "chat_id": self.chat_id,
                                "text": detail_msg,
                                "reply_to_message_id": main_msg_id,
                                "parse_mode": "HTML",
                            }
                            if topic_id:
                                reply_payload["message_thread_id"] = topic_id

                            result = await self._send_telegram_api(session, "sendMessage", payload=reply_payload)
                            if not result:
                                # Fallback message
                                fallback_msg = (
                                    "⚠️ 상세 변경 내용을 불러올 수 없습니다. <b>원본 공지 링크</b>를 확인해주세요."
                                )
                                reply_payload["text"] = fallback_msg
                                await self._send_telegram_api(session, "sendMessage", payload=reply_payload)

                    else:
                        # Diff generation failed but content changed
                        detail_msg = (
                            "⚠️ 내용이 변경되었으나 상세 비교를 생성할 수 없습니다."
                        )
                        reply_payload = {
                            "chat_id": self.chat_id,
                            "text": detail_msg,
                            "reply_to_message_id": main_msg_id,
                            "parse_mode": "HTML",
                        }
                        if topic_id:
                            reply_payload["message_thread_id"] = topic_id
                        
                        await self._send_telegram_api(session, "sendMessage", payload=reply_payload)

        return main_msg_id

    async def send_discord(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_thread_id: str = None,
        changes: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Sends a notice to Discord (Forum Channel preferred).
        Returns the Thread ID (or Message ID) if successful, None otherwise.
        """
        bot_token = settings.DISCORD_BOT_TOKEN
        channel_map = settings.DISCORD_CHANNEL_MAP

        if not bot_token or not channel_map:
            logger.warning("[NOTIFIER] Discord token or channel map missing")
            return None

        channel_id = channel_map.get(notice.site_key)
        logger.info(
            f"[NOTIFIER] Sending Discord notice. Site: {notice.site_key}, Channel: {channel_id}"
        )

        if channel_id:
            # 1. Try sending as a Forum Thread
            thread_url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
            message_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

            headers = {
                "Authorization": f"Bot {bot_token}",
                "User-Agent": "DiscordBot (https://github.com/yu-notice-bot, v1.0)",
            }

            # Prepare PDF previews
            # Prepare PDF previews
            pdf_previews = []
            if notice.attachments:
                for att in notice.attachments:
                    if getattr(att, "preview_images", None):
                        # Split into chunks of 10 (Discord limit)
                        preview_chunks = [
                            att.preview_images[i : i + 10]
                            for i in range(0, len(att.preview_images), 10)
                        ]

                        for chunk_idx, chunk in enumerate(preview_chunks):
                            # Add chunk suffix to filename if multiple chunks
                            filename_suffix = (
                                f" ({chunk_idx + 1}/{len(preview_chunks)})"
                                if len(preview_chunks) > 1
                                else ""
                            )
                            group = {
                                "filename": f"{att.name}{filename_suffix}",
                                "images": [],
                            }

                            for idx, img_data in enumerate(chunk):
                                global_idx = (chunk_idx * 10) + idx
                                group["images"].append(
                                    {
                                        "data": img_data,
                                        "filename": f"Preview_{att.name}_p{global_idx + 1}.jpg",
                                    }
                                )
                            pdf_previews.append(group)

            return await self._send_discord_common(
                session,
                notice,
                is_new,
                modified_reason,
                thread_url,
                message_url,
                headers,
                pdf_previews=pdf_previews,
                existing_thread_id=existing_thread_id,
                changes=changes,
            )
        else:
            logger.warning(
                f"[NOTIFIER] No Discord channel found for key '{notice.site_key}'"
            )
            return None

    async def _send_discord_common(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str,
        thread_url: str,
        message_url: str,
        headers: Dict,
        pdf_previews: List[Dict] = [],
        max_retries: int = 3,
        existing_thread_id: str = None,
        changes: Optional[Dict] = None,
    ) -> Optional[str]:
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
            "dormitory_menu": "기숙사식단",
        }
        site_name = site_name_map.get(notice.site_key, notice.site_key)

        # Thread Name (Title only - tags will show category)
        thread_name = f"{notice.title}"
        if len(thread_name) > 100:
            thread_name = thread_name[:97] + "..."

        # Use formatters module to create embed with category colors and icons
        from services.notification.formatters import create_discord_embed

        embed = create_discord_embed(notice, is_new, modified_reason, changes)

        # Add detailed change content (if available for modified notices)
        if modified_reason and notice.change_details:
            old_content = notice.change_details.get("old_content")
            new_content = notice.change_details.get("new_content")

            if old_content and new_content:
                diff_text = self.generate_clean_diff(old_content, new_content)

                if diff_text:
                    # Split if too long (Discord Field Limit 1024)
                    # Use 950 to allow for markdown wrapper
                    if len(diff_text) > constants.DISCORD_MAX_EMBED_LENGTH - 74:
                        chunks = [
                            diff_text[i : i + (constants.DISCORD_MAX_EMBED_LENGTH - 74)]
                            for i in range(0, len(diff_text), (constants.DISCORD_MAX_EMBED_LENGTH - 74))
                        ]
                        for idx, chunk in enumerate(chunks):
                            embed["fields"].append(
                                {
                                    "name": f"🔍 상세 변경 내용 ({idx + 1}/{len(chunks)})",
                                    "value": f"```diff\n{chunk}\n```",
                                    "inline": False,
                                }
                            )
                    else:
                        embed["fields"].append(
                            {
                                "name": "🔍 상세 변경 내용",
                                "value": f"```diff\n{diff_text}\n```",
                                "inline": False,
                            }
                        )

        # Add attachment links as the last field (before footer)
        if notice.attachments:
            attachment_links = ""
            for att in notice.attachments:
                fname = att.name
                ext = fname.split(".")[-1].lower() if "." in fname else ""
                emoji = constants.FILE_EMOJI_MAP.get(ext, constants.FILE_EMOJI_MAP["default"])
                attachment_links += f"{emoji} [{fname}]({att.url})\n"

            embed["fields"].append(
                {
                    "name": "📎 첨부파일",
                    "value": attachment_links.strip(),
                    "inline": False,
                }
            )

        # Download attachments using the SHARED session (to handle hotlink protection/cookies)
        attachment_files = []
        content_images = []

        # === 1. Content Images (from Body) ===
        # Fix: Only send content images if it's a new post OR images actually changed.
        should_send_content_images = is_new or (changes and "image" in changes)

        if notice.image_urls and should_send_content_images:
            for idx, image_url in enumerate(notice.image_urls):
                try:
                    async with session.get(
                        image_url,
                        headers={"Referer": notice.url},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as img_resp:
                        if img_resp.status == 200:
                            image_data = await img_resp.read()
                            content_images.append(
                                {
                                    "data": image_data,
                                    "filename": f"image_{idx}.jpg",
                                    "type": "content",
                                }
                            )
                            logger.info(
                                f"[NOTIFIER] Added Discord content image {idx + 1}/{len(notice.image_urls)}"
                            )
                except Exception as e:
                    logger.error(
                        f"[NOTIFIER] Failed to download image {idx} for Discord: {e}"
                    )

        # === 2. Attachments (Files) ===
        if notice.attachments:
            for idx, att in enumerate(notice.attachments[:10], 1):
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    try:
                        download_headers = {
                            "Referer": notice.url,
                            "User-Agent": settings.USER_AGENT,
                            "Accept": "*/*",
                            "Connection": "keep-alive",
                        }
                        # Use shared session for download
                        async with session.get(
                            att.url,
                            headers=download_headers,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as file_resp:
                            if file_resp.status == 200:
                                file_data = await file_resp.read()
                                file_size = len(file_data)
                                if file_size > constants.DISCORD_FILE_SIZE_LIMIT:
                                    break  # Skip > 25MB

                                actual_filename = att.name
                                # Try to get filename from Content-Disposition if available
                                if "Content-Disposition" in file_resp.headers:
                                    import re
                                    from urllib.parse import unquote

                                    match = re.search(
                                        r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)',
                                        file_resp.headers["Content-Disposition"],
                                    )
                                    if match:
                                        actual_filename = unquote(match.group(1))

                                logger.info(
                                    f"[NOTIFIER] Downloaded attachment: '{actual_filename}' ({file_size} bytes)"
                                )

                                attachment_files.append(
                                    {
                                        "data": file_data,
                                        "filename": actual_filename,
                                        "safe_filename": actual_filename,
                                        "url": att.url,
                                    }
                                )
                                break  # Success, exit retry loop
                            elif file_resp.status in [404, 403]:
                                logger.warning(
                                    f"[NOTIFIER] Failed to download {att.name}: Status {file_resp.status}"
                                )
                                break  # Don't retry for 404/403
                            else:
                                if attempt < max_retries:
                                    await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"[NOTIFIER] Error downloading {att.name}: {e}")
                        if attempt < max_retries:
                            await asyncio.sleep(1)

        # === 3. Prepare Files for Thread Starter vs Replies ===
        # Priority: Content Images > Embed > Previews > Attachments

        files_for_thread_starter = []
        files_for_attachments = attachment_files  # All attachments go to replies

        embed_image_data = None
        embed_image_filename = "image.png"

        # Logic for Content Images
        if len(content_images) == 1:
            # Case 1: Single Content Image -> Embed it
            first_image = content_images[0]
            embed_image_data = first_image["data"]
            embed_image_filename = first_image["filename"]
            embed["image"] = {"url": f"attachment://{embed_image_filename}"}
            logger.info(
                f"[NOTIFIER] Using single content image in Discord embed: {embed_image_filename}"
            )
        elif len(content_images) > 1:
            # Case 2: Multiple Content Images -> Send ALL as files with the Thread Starter
            # (Discord allows up to 10 files per message)
            files_for_thread_starter.extend(content_images)
            logger.info(
                f"[NOTIFIER] {len(content_images)} content images will be sent with Thread Starter"
            )
            # Do NOT set embed image, so they appear as a grid above/below the embed

        # PDF previews will be sent as separate messages (not attachments)
        # Do NOT add to attachment_files

        logger.info(
            f"[NOTIFIER] Thread Starter Files: {len(files_for_thread_starter)} | Attachments: {len(files_for_attachments)}"
        )

        # Prepare Payload
        # We need to construct the payload differently for Thread vs Message

        # 0. Handle Update Reply (if existing_thread_id)
        if not is_new and existing_thread_id:
            logger.info(
                f"[NOTIFIER] Sending update reply to existing thread: {existing_thread_id}"
            )

            # Construct Update Embed (Override the default one)
            update_embed = {
                "title": "⚠️ 공지사항 수정 알림",
                "description": f"**수정 사유:** {modified_reason}\n\n[원본 공지 보러가기]({notice.url})",
                "color": 0xFFA500,  # Orange
                "footer": {"text": "Yu Notice Bot • 업데이트됨"},
                "timestamp": datetime.utcnow().isoformat(),
            }
            update_embed["fields"] = []

            if notice.summary:
                update_embed["fields"].append(
                    {
                        "name": "📝 요약 (업데이트)",
                        "value": notice.summary[:1000],
                        "inline": False,
                    }
                )

            # Add Detailed Diff if available
            if notice.change_details:
                old_content = notice.change_details.get("old_content")
                new_content = notice.change_details.get("new_content")

                if old_content and new_content:
                    diff_text = self.generate_clean_diff(old_content, new_content)

                    if diff_text:
                        # Split if too long (Discord Field Limit 1024)
                        # Use 950 to allow for markdown wrapper
                        if len(diff_text) > 950:
                            chunks = [
                                diff_text[i : i + 950]
                                for i in range(0, len(diff_text), 950)
                            ]
                            for idx, chunk in enumerate(chunks):
                                update_embed["fields"].append(
                                    {
                                        "name": f"🔍 상세 변경 내용 ({idx + 1}/{len(chunks)})",
                                        "value": f"```diff\n{chunk}\n```",
                                        "inline": False,
                                    }
                                )
                        else:
                            update_embed["fields"].append(
                                {
                                    "name": "🔍 상세 변경 내용",
                                    "value": f"```diff\n{diff_text}\n```",
                                    "inline": False,
                                }
                            )

            # Prepare Payload
            payload = {"embeds": [update_embed]}

            # Determine if we need Multipart (Files) or JSON
            # For updates, we usually don't send content images again unless requested,
            # but here we just focus on the embed update.
            # If we want to support sending files on update, we can use files_for_thread_starter logic,
            # but typically updates just change text.
            # However, if we have files_for_attachments, we might want to send them?
            # The prompt implies priority for NEW posts mainly.
            # Let's keep update logic simple for now, or just support embed image if single.

            has_files_now = bool(embed_image_data)

            if has_files_now:
                form = MultipartWriter("form-data")
                self._add_text_part(form, "payload_json", json.dumps(payload))

                if embed_image_data:
                    filename = embed_image_filename
                    self._add_file_part(form, "files[0]", embed_image_data, filename)

                kwargs = {"data": form}
            else:
                kwargs = {"json": payload}

            # Send Reply
            reply_url = (
                f"https://discord.com/api/v10/channels/{existing_thread_id}/messages"
            )

            try:
                async with session.post(reply_url, headers=headers, **kwargs) as resp:
                    if resp.status in [200, 201]:
                        logger.info("[NOTIFIER] Discord update reply sent.")

                        # Send PDF previews if available AND relevant changes occurred
                        # Condition: New post OR Attachments changed OR Attachment Text changed
                        should_send_previews = is_new or (
                            changes
                            and any(
                                k in changes
                                for k in [
                                    "attachments",
                                    "attachments_added",
                                    "attachments_removed",
                                    "attachment_text",
                                ]
                            )
                        )

                        if pdf_previews and should_send_previews:
                            for group in pdf_previews:
                                await self._send_discord_pdf_preview_group(
                                    session, existing_thread_id, group, headers
                                )

                        # Send remaining files if any (Attachments)
                        if files_for_attachments:
                            await self._send_discord_reply(
                                session,
                                existing_thread_id,
                                files_for_attachments,
                                headers,
                                is_thread=True,
                            )

                        return existing_thread_id
                    elif resp.status == 404:
                        logger.warning(
                            f"[NOTIFIER] Thread {existing_thread_id} not found. Creating new thread."
                        )
                        # Fall through to create new thread
                    else:
                        logger.error(
                            f"[NOTIFIER] Failed to send update reply: {await resp.text()}"
                        )
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending update reply: {e}")

        created_thread_id = None
        created_message_id = None

        # Get tag IDs from AI-selected tags (for new threads only)
        tag_ids = []
        if is_new and notice.tags:
            tag_ids = TagMatcher.get_tag_ids(notice.tags, notice.site_key)
            if tag_ids:
                logger.info(
                    f"[NOTIFIER] Applying {len(tag_ids)} tags: {notice.tags} -> {tag_ids}"
                )
            else:
                logger.info(
                    f"[NOTIFIER] No tags matched for {notice.tags} (Site: {notice.site_key})"
                )

        # 1. Try Thread Creation (Forum)
        try:
            # Forum Thread Payload
            payload = {
                "name": thread_name,
                "message": {"embeds": [embed]},
                "auto_archive_duration": 4320,
            }  # 3 days

            # Apply matched tags if available
            if tag_ids:
                payload["applied_tags"] = tag_ids

            # Determine if we need Multipart (Files) or JSON
            # Files to send with Thread Starter:
            # 1. Single Content Image (Embed Image) -> files[0]
            # 2. Multiple Content Images -> files[0]...files[N]
            # Note: If single content image, it's in embed_image_data AND NOT in files_for_thread_starter (based on logic above)
            # If multiple, they are in files_for_thread_starter AND embed_image_data is None.

            has_files_now = bool(embed_image_data or files_for_thread_starter)

            if has_files_now:
                form = MultipartWriter("form-data")
                self._add_text_part(form, "payload_json", json.dumps(payload))

                file_idx = 0
                # Add Embed Image (if any)
                if embed_image_data:
                    self._add_file_part(form, f"files[{file_idx}]", embed_image_data, embed_image_filename)
                    file_idx += 1

                # Add Thread Starter Files (Multiple Content Images)
                for file_info in files_for_thread_starter:
                    self._add_file_part(
                        form, f"files[{file_idx}]", file_info["data"], file_info["filename"]
                    )
                    file_idx += 1

                kwargs = {"data": form}
            else:
                kwargs = {"json": payload}

            logger.info(f"[NOTIFIER] Sending Discord request to {thread_url}")
            async with session.post(thread_url, headers=headers, **kwargs) as resp:
                logger.info(f"[NOTIFIER] Discord response status: {resp.status}")
                if resp.status in [200, 201]:
                    logger.info(
                        f"[NOTIFIER] Discord Forum Thread created: {thread_name}"
                    )
                    resp_data = await resp.json()
                    created_thread_id = resp_data.get("id")
                    created_message_id = resp_data.get("id")
                    logger.info(f"[NOTIFIER] Created Thread ID: {created_thread_id}")

                    # If multiple content images, they were sent as attachments in the thread creation request
                    # If single content image, it's in the embed

                    # Send PDF previews as grouped messages
                    if created_thread_id and pdf_previews:
                        for group in pdf_previews:
                            await self._send_discord_pdf_preview_group(
                                session, created_thread_id, group, headers
                            )

                    # If we have attachments, send them to the thread (AFTER previews)
                    if files_for_attachments and created_thread_id:
                        await self._send_discord_reply(
                            session,
                            created_thread_id,
                            files_for_attachments,
                            headers,
                            is_thread=True,
                        )

                    return created_thread_id
                elif resp.status == 400 or resp.status == 404:
                    resp_text = await resp.text()
                    logger.warning(
                        f"[NOTIFIER] Failed to create thread (Status {resp.status}): {resp_text}. Fallback to normal message."
                    )
                else:
                    resp_text = await resp.text()
                    logger.error(
                        f"[NOTIFIER] Discord Thread creation failed: {resp_text}"
                    )
                    pass

        except Exception as e:
            logger.error(f"[NOTIFIER] Discord Thread error: {e}", exc_info=True)

        # 2. Fallback: Normal Message (Text Channel)
        try:
            payload = {"embeds": [embed]}

            has_files_now = bool(embed_image_data or files_for_thread_starter)

            if has_files_now:
                form = MultipartWriter("form-data")
                self._add_text_part(form, "payload_json", json.dumps(payload))

                file_idx = 0
                if embed_image_data:
                    self._add_file_part(form, f"files[{file_idx}]", embed_image_data, embed_image_filename)
                    file_idx += 1

                for file_info in files_for_thread_starter:
                    self._add_file_part(
                        form, f"files[{file_idx}]", file_info["data"], file_info["filename"]
                    )
                    file_idx += 1

                kwargs = {"data": form}
            else:
                kwargs = {"json": payload}

            async with session.post(message_url, headers=headers, **kwargs) as resp:
                if resp.status in [200, 204]:
                    logger.info(f"[NOTIFIER] Discord Message sent: {notice.title}")
                    resp_data = await resp.json()
                    created_message_id = resp_data.get("id")
                    channel_id = message_url.split("/")[
                        -2
                    ]  # Extract channel ID from URL

                    # If we have attachments, reply to the message
                    if files_for_attachments and created_message_id:
                        await self._send_discord_reply(
                            session,
                            channel_id,
                            files_for_attachments,
                            headers,
                            is_thread=False,
                            reply_to_id=created_message_id,
                        )

                    return created_message_id
                else:
                    logger.error(
                        f"[NOTIFIER] Discord Message failed: {await resp.text()}"
                    )
                    return None
        except Exception as e:
            logger.error(f"[NOTIFIER] Discord Message error: {e}")
            return None

    async def _send_discord_reply(
        self,
        session: aiohttp.ClientSession,
        channel_id: str,
        files: List[Dict],
        headers: Dict,
        is_thread: bool,
        reply_to_id: str = None,
    ):
        """
        Sends a reply (follow-up message) with attachments.
        """
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

        # Batch files (max 10 per message)
        for batch_idx in range(0, len(files), 10):
            batch = files[batch_idx : batch_idx + 10]

            form = MultipartWriter("form-data")
            payload = {}
            if reply_to_id and not is_thread:
                payload["message_reference"] = {"message_id": reply_to_id}

            self._add_text_part(form, "payload_json", json.dumps(payload))

            for idx, file_info in enumerate(batch):
                field_name = f"files[{idx}]"
                self._add_file_part(
                    form, field_name, file_info["data"], file_info["filename"]
                )

            try:
                async with session.post(url, headers=headers, data=form) as resp:
                    if resp.status not in [200, 201, 204]:
                        logger.error(
                            f"[NOTIFIER] Failed to send reply attachments: {await resp.text()}"
                        )
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending reply attachments: {e}")

    async def _send_discord_pdf_preview_group(
        self, session: aiohttp.ClientSession, thread_id: str, group: dict, headers: dict
    ):
        """Send a group of Discord PDF preview images as a single message."""
        try:
            message_url = f"https://discord.com/api/v10/channels/{thread_id}/messages"

            # Create caption with PDF filename
            original_filename = group.get("filename", "Preview.pdf")
            caption = f"📑 [미리보기] {original_filename}"

            form = MultipartWriter("form-data")
            self._add_text_part(form, "payload_json", json.dumps({"content": caption}))

            # Add all images in the group
            for idx, img in enumerate(group["images"]):
                self._add_file_part(form, f"files[{idx}]", img["data"], img["filename"])

            async with session.post(message_url, headers=headers, data=form) as resp:
                if resp.status in [200, 201]:
                    logger.info(
                        f"[NOTIFIER] Sent Discord PDF preview group: {caption} ({len(group['images'])} pages)"
                    )
                else:
                    logger.error(
                        f"[NOTIFIER] Failed to send Discord PDF preview group: {await resp.text()}"
                    )
        except Exception as e:
            logger.error(f"[NOTIFIER] Error sending Discord PDF preview group: {e}")

    async def send_menu_notification(
        self, session: aiohttp.ClientSession, notice: Notice, menu_data: Dict[str, Any]
    ):
        """
        Sends extracted menu text to Telegram and Pins it.
        """
        if not self.telegram_token:
            return
        # 1. Construct Message
        raw_text = menu_data.get("raw_text", "식단 정보 없음")
        start_date = menu_data.get("start_date", "")
        end_date = menu_data.get("end_date", "")

        msg = (
            f"🍱 <b>주간 기숙사 식단표</b>\n"
            f"📅 기간: {start_date} ~ {end_date}\n\n"
            f"{html.escape(raw_text)}\n\n"
            f"#Menu #식단"
        )

        # 2. Send to Telegram
        topic_id = settings.TELEGRAM_TOPIC_MAP.get(notice.site_key)
        payload = {"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}
        if topic_id:
            payload["message_thread_id"] = topic_id

        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                result = await resp.json()
                msg_id = result.get("result", {}).get("message_id")

                if msg_id:
                    logger.info(f"[NOTIFIER] Menu sent to Telegram: {msg_id}")

                    # 3. Pin Message
                    pin_payload = {"chat_id": self.chat_id, "message_id": msg_id}
                    async with session.post(
                        f"https://api.telegram.org/bot{self.telegram_token}/pinChatMessage",
                        json=pin_payload,
                    ) as pin_resp:
                        if pin_resp.status == 200:
                            logger.info("[NOTIFIER] Menu pinned successfully")
                        else:
                            logger.warning(
                                f"[NOTIFIER] Failed to pin menu: {await pin_resp.text()}"
                            )

        except Exception as e:
            logger.error(f"[NOTIFIER] Failed to send/pin menu: {e}")

        # 4. Send to Discord (Optional, just text embed)
        # Reuse send_discord logic or create custom embed if needed
        # For now, we rely on the main notice notification for Discord which includes the image.
        # We can add a follow-up text embed if requested.
