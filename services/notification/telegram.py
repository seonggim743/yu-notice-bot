"""
Telegram notification service.
"""
import aiohttp
import json
import asyncio
import html
from typing import Dict, List, Optional, Any

from aiohttp import MultipartWriter

from core.config import settings
from core.logger import get_logger
from core import constants
from models.notice import Notice
from services.notification.base import BaseNotifier
from services.notification.formatters import create_telegram_message

logger = get_logger(__name__)


class TelegramNotifier(BaseNotifier):
    """Handles all Telegram-specific notification logic."""

    def __init__(self):
        self.telegram_token = settings.TELEGRAM_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

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
                                        r'filename\*?=["\']?(?:UTF-8\'\')?(["\';]+)',
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
