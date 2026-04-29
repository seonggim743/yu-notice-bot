"""
Telegram notification service.
Implements NotificationChannel interface for Strategy Pattern.
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
from services.file.attachment_downloader import AttachmentDownloader
from services.notification.base import BaseNotifier, NotificationChannel
from services.notification.diff_chunker import split_diff
from services.notification.formatters import create_telegram_message
from services.file.image import ImageHandler

from services.notification.dev_notifier import DevNotifier

# Telegram messages cap at 4096; reserve room for the header/code wrapper.
_TELEGRAM_DIFF_CHUNK_LIMIT = constants.TELEGRAM_MAX_MESSAGE_LENGTH - 96

logger = get_logger(__name__)


class TelegramNotifier(BaseNotifier, NotificationChannel):
    """
    Handles all Telegram-specific notification logic.
    Implements NotificationChannel interface for Strategy Pattern compatibility.
    """
    
    @property
    def channel_name(self) -> str:
        return "telegram"

    def __init__(self):
        self.telegram_token = settings.TELEGRAM_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.image_handler = ImageHandler()
        self.dev_notifier = DevNotifier()
        self.downloader = AttachmentDownloader()
    
    def is_enabled(self) -> bool:
        """Check if Telegram is configured and enabled."""
        return bool(self.telegram_token and self.chat_id)
    
    async def send_notice(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[Any] = None,
        changes: Optional[Dict] = None,
    ) -> Optional[Any]:
        """
        Strategy Pattern interface method.
        Delegates to send_telegram for actual implementation.
        """
        return await self.send_telegram(
            session=session,
            notice=notice,
            is_new=is_new,
            modified_reason=modified_reason,
            existing_message_id=existing_message_id,
            changes=changes,
        )

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
        reply_fallback_used = False
        
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
                            resp_text = await resp.text()
                            if (
                                method == "sendMessage"
                                and resp.status == 400
                                and payload
                                and payload.get("reply_to_message_id")
                                and not reply_fallback_used
                            ):
                                logger.warning(
                                    "[NOTIFIER] Telegram reply target unavailable. "
                                    "Retrying sendMessage as a new message."
                                )
                                payload = dict(payload)
                                payload.pop("reply_to_message_id", None)
                                reply_fallback_used = True
                                continue

                            logger.error(
                                f"[NOTIFIER] Telegram API {method} failed (Status {resp.status}): {resp_text}"
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

    async def send_canvas_message(
        self,
        session: aiohttp.ClientSession,
        text: str,
        topic_id: Optional[int] = None,
        attachment_payloads: Optional[List[Dict[str, Any]]] = None,
        use_html: bool = False,
        title: Optional[str] = None,
        url: Optional[str] = None,
        attachments: Optional[List[Any]] = None,
        event_kind: Optional[str] = None,
        is_modified: Optional[bool] = None,
    ) -> Optional[int]:
        """Send a Canvas notification. Returns Telegram message_id.

        When `use_html` is True the message is sent with parse_mode="HTML"
        so <b>/<blockquote> tags render. Caller must have escaped any
        user-supplied content (canvas_formatter handles this).

        For each entry in `attachment_payloads` we send:
        1. preview image batches (≤10 photos per Telegram media group)
           with caption "📑 [미리보기] {filename} (N/M)"
        2. the original file as a sendDocument reply with caption
           "📎 [원본] {filename} ({size})"

        All extra messages are sent as replies to the main message so
        Telegram threads them visually.
        """
        if not text:
            return None
        text = self._format_canvas_text(
            text,
            use_html=use_html,
            title=title,
            url=url,
            event_kind=event_kind,
            is_modified=is_modified,
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if use_html:
            payload["parse_mode"] = "HTML"
        if topic_id:
            payload["message_thread_id"] = topic_id
        inline_keyboard = self._canvas_attachment_keyboard(
            attachments, attachment_payloads
        )
        if inline_keyboard:
            payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})
        result = await self._send_telegram_api(session, "sendMessage", payload=payload)
        if not (result and result.get("ok")):
            return None

        message_id = result.get("result", {}).get("message_id")
        if attachment_payloads and message_id:
            for entry in attachment_payloads:
                await self._send_canvas_attachment_entry(
                    session, entry, topic_id, message_id
                )
        return message_id

    def _format_canvas_text(
        self,
        text: str,
        use_html: bool,
        title: Optional[str],
        url: Optional[str],
        event_kind: Optional[str],
        is_modified: Optional[bool],
    ) -> str:
        prefix = self._canvas_status_prefix(event_kind, is_modified)
        if prefix and not text.startswith(prefix):
            text = f"{prefix} {text}"

        if not (use_html and title and url):
            return text

        safe_title = html.escape(title, quote=False)
        safe_url = html.escape(url, quote=True)
        linked_title = f'<a href="{safe_url}"><b>{safe_title}</b></a>'
        return text.replace(f"<b>{safe_title}</b>", linked_title, 1)

    @staticmethod
    def _canvas_status_prefix(
        event_kind: Optional[str], is_modified: Optional[bool]
    ) -> str:
        if is_modified is True or event_kind in {
            "assignment_modified",
            "due_date_changed",
        }:
            return "🔄"
        if event_kind in {"new_assignment", "new_announcement"}:
            return "🆕"
        return ""

    @staticmethod
    def _canvas_attachment_keyboard(
        attachments: Optional[List[Any]],
        attachment_payloads: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[List[Dict[str, str]]]]:
        buttons = []
        for att in attachments or []:
            name = getattr(att, "display_name", "") or "첨부파일"
            att_url = getattr(att, "url", "")
            if att_url:
                buttons.append({"text": name, "url": att_url})

        if not buttons:
            for entry in attachment_payloads or []:
                name = entry.get("source_filename") or "첨부파일"
                att_url = entry.get("source_url")
                if att_url:
                    buttons.append({"text": name, "url": att_url})

        return [[button] for button in buttons] if buttons else None

    async def _send_canvas_attachment_entry(
        self,
        session: aiohttp.ClientSession,
        entry: Dict[str, Any],
        topic_id: Optional[int],
        reply_to_message_id: int,
    ) -> None:
        """Send one source-file's previews + original file as replies."""
        source_filename = entry.get("source_filename") or "첨부파일"
        previews = entry.get("preview_images") or []
        original_data = entry.get("original_data")
        source_size = int(entry.get("source_size") or 0)

        # 1. Preview chunks (Telegram media group max 10).
        chunks = [previews[i : i + 10] for i in range(0, len(previews), 10)] or []
        total_chunks = len(chunks)
        for chunk_idx, chunk in enumerate(chunks):
            caption = self._preview_caption(source_filename, chunk_idx, total_chunks)
            form = MultipartWriter("form-data")
            self._add_text_part(form, "chat_id", self.chat_id)
            self._add_text_part(form, "reply_to_message_id", reply_to_message_id)
            if topic_id:
                self._add_text_part(form, "message_thread_id", topic_id)

            media = []
            for idx, image in enumerate(chunk):
                field_name = f"preview_{idx}"
                item = {"type": "photo", "media": f"attach://{field_name}"}
                # Telegram puts the album-level caption on the first item.
                if idx == 0:
                    item["caption"] = caption
                media.append(item)
                self._add_file_part(
                    form,
                    field_name,
                    image["data"],
                    image.get("filename") or f"preview_{idx + 1}.jpg",
                    content_type="image/jpeg",
                )
            self._add_text_part(form, "media", json.dumps(media))
            await self._send_telegram_api(session, "sendMediaGroup", data=form)

        # 2. Original file (skip if missing or larger than Telegram's limit).
        if original_data and source_size <= constants.TELEGRAM_FILE_SIZE_LIMIT:
            doc_form = MultipartWriter("form-data")
            self._add_text_part(doc_form, "chat_id", self.chat_id)
            self._add_text_part(doc_form, "reply_to_message_id", reply_to_message_id)
            if topic_id:
                self._add_text_part(doc_form, "message_thread_id", topic_id)
            self._add_text_part(
                doc_form,
                "caption",
                self._original_caption(source_filename, source_size),
            )
            self._add_file_part(
                doc_form,
                "document",
                original_data,
                source_filename,
            )
            await self._send_telegram_api(session, "sendDocument", data=doc_form)
        elif original_data:
            logger.info(
                f"[NOTIFIER] Skipping original-file forward for {source_filename}: "
                f"{source_size} bytes exceeds Telegram limit "
                f"{constants.TELEGRAM_FILE_SIZE_LIMIT}"
            )

    @staticmethod
    def _preview_caption(filename: str, chunk_idx: int, total_chunks: int) -> str:
        suffix = f" ({chunk_idx + 1}/{total_chunks})" if total_chunks > 1 else ""
        return f"📑 [미리보기] {filename}{suffix}"

    @staticmethod
    def _original_caption(filename: str, size_bytes: int) -> str:
        return f"📎 [원본] {filename} ({TelegramNotifier._format_byte_size(size_bytes)})"


    @staticmethod
    def _format_byte_size(size_bytes: int) -> str:
        """Render a byte count as 1.2KB / 3.4MB."""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        kb = size_bytes / 1024
        if kb < 1024:
            return f"{kb:.0f}KB"
        return f"{kb / 1024:.1f}MB"

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
        truncate_suffix = "\n\n...전체 내용은 원문 링크를 확인해주세요."
        max_message_length = constants.TELEGRAM_MAX_MESSAGE_LENGTH
        if len(msg) > max_message_length:
            logger.warning(
                f"[NOTIFIER] Telegram message too long ({len(msg)} chars). Truncating."
            )
            msg = msg[: max_message_length - len(truncate_suffix)] + truncate_suffix

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
            downloaded_images = await self.downloader.download_content_images(
                session, notice.image_urls, referer=notice.url
            )
            for slot, (idx, original_data) in enumerate(downloaded_images):
                # Optimize for Telegram (Resize if too big)
                optimized_data = self.image_handler.optimize_for_telegram(original_data)

                # Only first image gets the main caption
                caption = msg if slot == 0 else None
                content_images_to_send.append(
                    {
                        "type": "content",
                        "data": optimized_data,
                        "original_data": original_data,
                        "filename": f"image_{idx}.jpg",  # Force jpg extension
                        "caption": caption,
                    }
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
            
            # 1. Send Preview (Resized) via sendPhoto
            form = MultipartWriter("form-data")
            self._add_file_part(form, "photo", img["data"], img["filename"]) # Already optimized in loop
            self._add_text_part(
                form, "caption", img["caption"][: constants.DISCORD_MAX_EMBED_LENGTH]
            )
            self._add_text_part(form, "parse_mode", "HTML")
            self._add_text_part(form, "chat_id", str(self.chat_id))
            if topic_id:
                self._add_text_part(form, "message_thread_id", str(topic_id))
            if buttons:
                self._add_text_part(
                    form, "reply_markup", json.dumps({"inline_keyboard": inline_keyboard})
                )
            if not is_new and existing_message_id:
                self._add_text_part(form, "reply_to_message_id", str(existing_message_id))

            result = await self._send_telegram_api(session, "sendPhoto", data=form)
            
            # Fallback & Dev Alert
            if not result:
                error_msg = f"Telegram sendPhoto failed for {notice.site_key} - {notice.title}"
                logger.warning(f"[TELEGRAM] {error_msg}. Falling back to sendMessage.")
                await self.dev_notifier.send_alert(error_msg + "\n(Falling back to Text)")

                fallback_text = f"{msg}"
                if buttons:
                     fallback_text += "\n\n(이미지 전송 실패로 텍스트로 대체됨)"
                
                payload = {
                    "chat_id": str(self.chat_id),
                    "text": fallback_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False
                }
                if topic_id:
                    payload["message_thread_id"] = str(topic_id)
                if buttons:
                    payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})
                
                result = await self._send_telegram_api(session, "sendMessage", payload=payload)
            if result:
                main_msg_id = result.get("result", {}).get("message_id")
                
                # 2. Dual Send: Send Original as Document
                if img.get("original_data"):
                    # Only send if original is different or if explicitly requested (User wants both)
                    # Let's send it always as requested.
                    doc_form = MultipartWriter("form-data")
                    self._add_file_part(doc_form, "document", img["original_data"], "original_" + img["filename"])
                    self._add_text_part(doc_form, "caption", "📂 원본 이미지 파일")
                    self._add_text_part(doc_form, "chat_id", str(self.chat_id))
                    if topic_id:
                        self._add_text_part(doc_form, "message_thread_id", str(topic_id))
                    if main_msg_id:
                         self._add_text_part(doc_form, "reply_to_message_id", str(main_msg_id))

                    await self._send_telegram_api(session, "sendDocument", data=doc_form)
                    logger.info("[TELEGRAM] Sent original image as document (Dual Send)")

        else:
            # Multiple Photos or Mix of Content + Previews
            # Send content images (if any)
            if content_images_to_send:
                # Case A: Single Content Image -> Use sendPhoto (MediaGroup requires 2+)
                if len(content_images_to_send) == 1:
                    img = content_images_to_send[0]
                    form = MultipartWriter("form-data")
                    self._add_file_part(form, "photo", img["data"], img["filename"])
                    if img.get("caption"):
                        self._add_text_part(form, "caption", img["caption"][: constants.DISCORD_MAX_EMBED_LENGTH])
                        self._add_text_part(form, "parse_mode", "HTML")
                    self._add_text_part(form, "chat_id", str(self.chat_id))
                    if topic_id:
                        self._add_text_part(form, "message_thread_id", str(topic_id))
                    
                    # If updating, reply to existing message
                    if not is_new and existing_message_id:
                        self._add_text_part(form, "reply_to_message_id", str(existing_message_id))

                    result = await self._send_telegram_api(session, "sendPhoto", data=form)
                    
                    # Fallback: If photo invalid (e.g. Dimensions), send as text
                    if not result:
                        logger.warning("[TELEGRAM] sendPhoto failed. Falling back to sendMessage.")
                        # Use the already-formatted message (msg) instead of undefined text_content
                        fallback_text = msg
                        
                        payload = {
                            "chat_id": str(self.chat_id),
                            "text": fallback_text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": False
                        }
                        if topic_id:
                            payload["message_thread_id"] = str(topic_id)
                        
                        result = await self._send_telegram_api(session, "sendMessage", payload=payload)
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

                            total_chunks = len(preview_chunks)
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
                                    # Per-chunk caption with (N/M) suffix when split.
                                    if idx == 0:
                                        suffix = (
                                            f" ({chunk_idx + 1}/{total_chunks})"
                                            if total_chunks > 1
                                            else ""
                                        )
                                        media_item["caption"] = (
                                            f"📑 [미리보기] {att.name}{suffix}"
                                        )
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
            collected_files = await self.downloader.download_attachments(
                session,
                notice.attachments,
                file_size_limit=constants.TELEGRAM_FILE_SIZE_LIMIT,
                referer=notice.url,
            )

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
                diff_text = self.generate_clean_diff(
                    old_content, new_content, inline_style="telegram"
                )

                if diff_text:
                    chunks = split_diff(diff_text, _TELEGRAM_DIFF_CHUNK_LIMIT)
                    for idx, chunk in enumerate(chunks):
                        header = (
                            "🔍 <b>상세 변경 내용</b>"
                            if len(chunks) == 1
                            else f"🔍 <b>상세 변경 내용 ({idx + 1}/{len(chunks)})</b>"
                        )
                        detail_msg = f"{header}\n{chunk}"
                        reply_payload = {
                            "chat_id": self.chat_id,
                            "text": detail_msg,
                            "reply_to_message_id": main_msg_id,
                            "parse_mode": "HTML",
                        }
                        if topic_id:
                            reply_payload["message_thread_id"] = topic_id

                        result = await self._send_telegram_api(
                            session, "sendMessage", payload=reply_payload
                        )
                        if result:
                            if idx < len(chunks) - 1:
                                await asyncio.sleep(0.2)
                        elif len(chunks) == 1:
                            # Single-chunk path: fall back to a short notice
                            fallback_msg = (
                                "⚠️ 상세 변경 내용을 불러올 수 없습니다. <b>원본 공지 링크</b>를 확인해주세요."
                            )
                            reply_payload["text"] = fallback_msg
                            await self._send_telegram_api(
                                session, "sendMessage", payload=reply_payload
                            )

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

                    await self._send_telegram_api(
                        session, "sendMessage", payload=reply_payload
                    )

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
