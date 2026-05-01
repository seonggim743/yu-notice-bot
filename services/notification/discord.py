"""
Discord notification service.
Implements NotificationChannel interface for Strategy Pattern.
"""
import aiohttp
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any

from aiohttp import MultipartWriter

from core.config import settings
from core.logger import get_logger
from core import constants
from core.utils import get_utc_now
from models.notice import Notice
from services.file.attachment_downloader import AttachmentDownloader
from services.notification.base import BaseNotifier, NotificationChannel
from services.notification.diff_chunker import split_diff
from services.notification.formatters import (
    create_discord_embed,
    create_revised_body_quote_field,
    format_change_summary,
)
from services.tag_matcher import TagMatcher

# Discord embed field max is 1024; inline bold diff is sent as plain field text.
_DISCORD_DIFF_CHUNK_LIMIT = constants.DISCORD_MAX_EMBED_LENGTH

logger = get_logger(__name__)


def _format_byte_size_discord(size_bytes: int) -> str:
    """Render a byte count as 1.2KB / 3.4MB. Mirrors telegram._format_byte_size
    so caption text stays consistent across channels."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f}KB"
    return f"{kb / 1024:.1f}MB"


class DiscordNotifier(BaseNotifier, NotificationChannel):
    """
    Handles all Discord-specific notification logic.
    Implements NotificationChannel interface for Strategy Pattern compatibility.
    """

    MAX_RATE_LIMIT_RETRIES = 2

    def __init__(self):
        self.downloader = AttachmentDownloader()

    @property
    def channel_name(self) -> str:
        return "discord"

    @asynccontextmanager
    async def _discord_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs,
    ):
        """Issue a Discord API request, transparently retrying on HTTP 429.

        Honors the Retry-After response header (seconds) and retries up to
        MAX_RATE_LIMIT_RETRIES times. On the final attempt the response is
        yielded regardless of status so callers can handle the failure.
        """
        for attempt in range(self.MAX_RATE_LIMIT_RETRIES + 1):
            is_last = attempt >= self.MAX_RATE_LIMIT_RETRIES
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 429 and not is_last:
                    retry_after = float(resp.headers.get("Retry-After", "1") or 1)
                    logger.warning(
                        f"[NOTIFIER] Discord rate limited (429). "
                        f"Sleeping {retry_after}s before retry {attempt + 1}/{self.MAX_RATE_LIMIT_RETRIES}."
                    )
                    await asyncio.sleep(retry_after)
                    continue
                yield resp
                return

    def is_enabled(self) -> bool:
        """Check if Discord is configured and enabled."""
        return bool(settings.DISCORD_BOT_TOKEN and settings.DISCORD_CHANNEL_MAP)

    async def send_canvas_message(
        self,
        session: aiohttp.ClientSession,
        text: str,
        channel_id: Optional[str] = None,
        event_kind: Optional[str] = None,
        attachment_payloads: Optional[List[Dict[str, Any]]] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        attachments: Optional[List[Any]] = None,
        is_modified: Optional[bool] = None,
    ) -> Optional[str]:
        """Send a Canvas notification embed. Returns Discord message id.

        For each entry in `attachment_payloads`:
        1. preview image batches (≤10 files per Discord message) sent as
           replies with content "📑 [미리보기] {filename} (N/M)"
        2. original file sent as a reply with content
           "📎 [원본] {filename} ({size})"
        """
        if not text or not channel_id:
            return None
        message_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        auth_headers = {
            "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
        }
        json_headers = {
            **auth_headers,
            "Content-Type": "application/json",
        }
        description = self._truncate_canvas_description(text)
        embed = {
            "description": description,
            "color": self._canvas_embed_color(event_kind),
            "timestamp": get_utc_now().isoformat(),
        }
        if title:
            embed["title"] = (
                "⚠️ 공지사항 수정 알림"
                if self._canvas_is_modified(event_kind, is_modified)
                else self._truncate_canvas_title(title)
            )
        if url:
            embed["url"] = url

        attachment_field = self._canvas_attachment_field(
            attachments, attachment_payloads
        )
        if attachment_field:
            embed["fields"] = [attachment_field]

        payload = {"embeds": [embed]}
        async with self._discord_request(
            session, "POST", message_url, headers=json_headers, json=payload
        ) as resp:
            if resp.status not in (200, 201):
                logger.error(
                    f"[NOTIFIER] Discord canvas send failed (status {resp.status}): "
                    f"{await resp.text()}"
                )
                return None
            data = await resp.json()
            message_id = data.get("id")

        if attachment_payloads and message_id:
            for entry in attachment_payloads:
                await self._send_canvas_attachment_entry(
                    session, channel_id, auth_headers, entry, message_id
                )
        return message_id

    async def _send_canvas_attachment_entry(
        self,
        session: aiohttp.ClientSession,
        channel_id: str,
        auth_headers: Dict[str, str],
        entry: Dict[str, Any],
        reply_to_id: str,
    ) -> None:
        """Send one source-file's previews + original file as Discord replies."""
        source_filename = entry.get("source_filename") or "첨부파일"
        previews = entry.get("preview_images") or []
        original_data = entry.get("original_data")
        source_size = int(entry.get("source_size") or 0)

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

        # 1. Preview chunks (Discord allows up to 10 files per message).
        chunks = [previews[i : i + 10] for i in range(0, len(previews), 10)] or []
        total_chunks = len(chunks)
        for chunk_idx, chunk in enumerate(chunks):
            content = self._preview_caption(source_filename, chunk_idx, total_chunks)
            form = MultipartWriter("form-data")
            payload = self._canvas_reply_payload(channel_id, reply_to_id, content)
            self._add_text_part(form, "payload_json", json.dumps(payload))
            for idx, image in enumerate(chunk):
                self._add_file_part(
                    form,
                    f"files[{idx}]",
                    image["data"],
                    image.get("filename") or f"preview_{idx + 1}.jpg",
                )
            try:
                async with self._discord_request(
                    session, "POST", url, headers=auth_headers, data=form
                ) as resp:
                    if resp.status not in (200, 201, 204):
                        logger.error(
                            f"[NOTIFIER] Discord preview send failed: {await resp.text()}"
                        )
            except Exception as e:
                logger.error(f"[NOTIFIER] Discord preview send error: {e}")

        # 2. Original file (skip if missing or larger than Discord's limit).
        if original_data and source_size <= constants.DISCORD_FILE_SIZE_LIMIT:
            content = self._original_caption(source_filename, source_size)
            form = MultipartWriter("form-data")
            payload = self._canvas_reply_payload(channel_id, reply_to_id, content)
            self._add_text_part(form, "payload_json", json.dumps(payload))
            self._add_file_part(form, "files[0]", original_data, source_filename)
            try:
                async with self._discord_request(
                    session, "POST", url, headers=auth_headers, data=form
                ) as resp:
                    if resp.status not in (200, 201, 204):
                        logger.error(
                            f"[NOTIFIER] Discord original-file send failed: {await resp.text()}"
                        )
            except Exception as e:
                logger.error(f"[NOTIFIER] Discord original-file send error: {e}")
        elif original_data:
            logger.info(
                f"[NOTIFIER] Skipping original-file forward for {source_filename}: "
                f"{source_size} bytes exceeds Discord limit "
                f"{constants.DISCORD_FILE_SIZE_LIMIT}"
            )

    @staticmethod
    def _preview_caption(filename: str, chunk_idx: int, total_chunks: int) -> str:
        suffix = f" ({chunk_idx + 1}/{total_chunks})" if total_chunks > 1 else ""
        return f"📑 [미리보기] {filename}{suffix}"

    @staticmethod
    def _original_caption(filename: str, size_bytes: int) -> str:
        return f"📎 [원본] {filename} ({_format_byte_size_discord(size_bytes)})"

    @staticmethod
    def _canvas_reply_payload(
        channel_id: str, reply_to_id: str, content: str
    ) -> Dict[str, Any]:
        """Build a Discord reply payload for Canvas attachment follow-ups."""
        return {
            "content": content,
            "message_reference": {
                "message_id": str(reply_to_id),
                "channel_id": str(channel_id),
                "fail_if_not_exists": False,
            },
            "allowed_mentions": {"replied_user": False},
        }

    @staticmethod
    def _canvas_is_modified(
        event_kind: Optional[str], is_modified: Optional[bool]
    ) -> bool:
        return bool(
            is_modified is True
            or event_kind in {"assignment_modified", "due_date_changed"}
        )

    @staticmethod
    def _truncate_canvas_title(title: str) -> str:
        return title if len(title) <= 256 else title[:253].rstrip() + "..."

    def _canvas_attachment_field(
        self,
        attachments: Optional[List[Any]],
        attachment_payloads: Optional[List[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        lines = []
        for att in attachments or []:
            name = getattr(att, "display_name", "") or "첨부파일"
            att_url = getattr(att, "url", "")
            if att_url:
                lines.append(f"{self._attachment_emoji(name)} [{name}]({att_url})")

        if not lines:
            for entry in attachment_payloads or []:
                name = entry.get("source_filename") or "첨부파일"
                att_url = entry.get("source_url")
                if att_url:
                    lines.append(f"{self._attachment_emoji(name)} [{name}]({att_url})")

        if not lines:
            return None
        return {
            "name": "📎 첨부파일",
            "value": "\n".join(lines)[: constants.DISCORD_MAX_EMBED_LENGTH],
            "inline": False,
        }

    @staticmethod
    def _attachment_emoji(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "default"
        return constants.FILE_EMOJI_MAP.get(ext, constants.FILE_EMOJI_MAP["default"])

    @staticmethod
    def _canvas_embed_color(event_kind: Optional[str]) -> int:
        """Map Canvas event kind to Discord embed color."""
        if event_kind == "new_assignment":
            return 0x3498DB  # blue
        if event_kind in {"assignment_modified", "due_date_changed"}:
            return 0xE67E22  # orange
        if event_kind == "new_announcement":
            return 0x2ECC71  # green
        if event_kind == "grade_registered":
            return 0x9B59B6  # purple
        if event_kind in {"deadline_reminder", "unsubmitted_warning"}:
            return 0xE74C3C  # red
        return 0x95A5A6  # neutral grey

    @staticmethod
    def _truncate_canvas_description(text: str) -> str:
        """Discord embed descriptions cap at 4096 characters."""
        limit = 4096
        suffix = "\n\n...open Canvas for the full content."
        if len(text) <= limit:
            return text
        return text[: limit - len(suffix)].rstrip() + suffix

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
        Delegates to send_discord for actual implementation.
        """
        return await self.send_discord(
            session=session,
            notice=notice,
            is_new=is_new,
            modified_reason=modified_reason,
            existing_thread_id=existing_message_id,  # Discord uses thread_id
            changes=changes,
        )

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
        embed = create_discord_embed(notice, is_new, modified_reason, changes)

        # Add detailed change content (if available for modified notices)
        if modified_reason and notice.change_details:
            old_content = notice.change_details.get("old_content")
            new_content = notice.change_details.get("new_content")

            if old_content and new_content:
                diff_text = self.generate_clean_diff(
                    old_content, new_content, inline_style="discord"
                )

                if diff_text:
                    chunks = split_diff(diff_text, _DISCORD_DIFF_CHUNK_LIMIT)
                    for idx, chunk in enumerate(chunks):
                        name = (
                            "🔍 상세 변경 내용"
                            if len(chunks) == 1
                            else f"🔍 상세 변경 내용 ({idx + 1}/{len(chunks)})"
                        )
                        embed["fields"].append(
                            {
                                "name": name,
                                "value": chunk,
                                "inline": False,
                            }
                        )
                    revised_field = create_revised_body_quote_field(new_content)
                    if revised_field:
                        embed["fields"].append(revised_field)

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
        content_images = []

        # === 1. Content Images (from Body) ===
        # Fix: Only send content images if it's a new post OR images actually changed.
        should_send_content_images = is_new or (changes and "image" in changes)

        if notice.image_urls and should_send_content_images:
            downloaded_images = await self.downloader.download_content_images(
                session, notice.image_urls, referer=notice.url
            )
            for idx, image_data in downloaded_images:
                content_images.append(
                    {
                        "data": image_data,
                        "filename": f"image_{idx}.jpg",
                        "type": "content",
                    }
                )

        # === 2. Attachments (Files) ===
        downloaded_attachments = await self.downloader.download_attachments(
            session,
            notice.attachments,
            file_size_limit=constants.DISCORD_FILE_SIZE_LIMIT,
            referer=notice.url,
        )
        attachment_files = [
            {"data": data, "filename": filename}
            for filename, data in downloaded_attachments
        ]

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
                "timestamp": get_utc_now().isoformat(),
            }
            update_embed["fields"] = []

            # Add Change Summary Field (Unified Style)
            change_summary = ""
            if changes:
                change_summary = format_change_summary(changes)
            
            if change_summary:
                update_embed["fields"].append(
                    {"name": "🔄 변경 요약", "value": change_summary, "inline": False}
                )

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
                    diff_text = self.generate_clean_diff(
                        old_content, new_content, inline_style="discord"
                    )

                    if diff_text:
                        chunks = split_diff(diff_text, _DISCORD_DIFF_CHUNK_LIMIT)
                        for idx, chunk in enumerate(chunks):
                            name = (
                                "🔍 상세 변경 내용"
                                if len(chunks) == 1
                                else f"🔍 상세 변경 내용 ({idx + 1}/{len(chunks)})"
                            )
                            update_embed["fields"].append(
                                {
                                    "name": name,
                                    "value": chunk,
                                    "inline": False,
                                }
                            )
                    revised_field = create_revised_body_quote_field(new_content)
                    if revised_field:
                        update_embed["fields"].append(revised_field)

            # Prepare Payload
            payload = {"embeds": [update_embed]}

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
                async with self._discord_request(session, "POST", reply_url, headers=headers, **kwargs) as resp:
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

                        update_message_id = None
                        try:
                            update_message_id = (await resp.json()).get("id")
                        except Exception:
                            update_message_id = None
                        reply_anchor_id = update_message_id or existing_thread_id

                        if pdf_previews and should_send_previews:
                            for group in pdf_previews:
                                await self._send_discord_pdf_preview_group(
                                    session,
                                    existing_thread_id,
                                    group,
                                    headers,
                                    reply_to_id=reply_anchor_id,
                                )

                        # Send remaining files if any (Attachments)
                        if files_for_attachments:
                            await self._send_discord_reply(
                                session,
                                existing_thread_id,
                                files_for_attachments,
                                headers,
                                is_thread=True,
                                reply_to_id=reply_anchor_id,
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

        # Get tag IDs from AI-selected tags (for new threads only).
        # Tag matching is optional; thread/message creation runs regardless.
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

        # === SPLIT EMBED LOGIC ===
        # Discord limit: 6000 chars total. We must split if it exceeds this.
        embeds_to_send = self._split_embed(embed)

        main_embed = embeds_to_send[0]
        followup_embeds = embeds_to_send[1:]

        # 1. Try Thread Creation (Forum)
        try:
            # Forum Thread Payload (First Embed)
            payload = {
                "name": thread_name,
                "message": {"embeds": [main_embed]},
                "auto_archive_duration": 4320,
            }  # 3 days

            # Apply matched tags if available
            if tag_ids:
                payload["applied_tags"] = tag_ids

            # Determine if we need Multipart (Files) or JSON
            # Files generally go with the FIRST message (Thread Starter) if possible
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
            async with self._discord_request(session, "POST", thread_url, headers=headers, **kwargs) as resp:
                logger.info(f"[NOTIFIER] Discord response status: {resp.status}")
                if resp.status in [200, 201]:
                    logger.info(
                        f"[NOTIFIER] Discord Forum Thread created: {thread_name}"
                    )
                    resp_data = await resp.json()
                    created_thread_id = resp_data.get("id")
                    created_message_id = resp_data.get("id")
                    logger.info(f"[NOTIFIER] Created Thread ID: {created_thread_id}")

                    # --- Send Follow-up Embeds (Split Parts) ---
                    if hasattr(self, "_send_discord_reply") and created_thread_id and followup_embeds:
                        for idx, f_embed in enumerate(followup_embeds):
                            try:
                                # Send as simple message with embed
                                f_payload = {"embeds": [f_embed]}
                                f_url = f"https://discord.com/api/v10/channels/{created_thread_id}/messages"
                                async with self._discord_request(session, "POST", f_url, headers=headers, json=f_payload) as f_resp:
                                    if f_resp.status not in [200, 201]:
                                        logger.error(f"[NOTIFIER] Failed to send followup embed {idx+1}: {await f_resp.text()}")
                                await asyncio.sleep(0.5) # Rate limit safety
                            except Exception as e:
                                logger.error(f"[NOTIFIER] Error sending followup embed: {e}")
                    # ------------------------------------------

                    # Send PDF previews as grouped messages
                    if created_thread_id and pdf_previews:
                        for group in pdf_previews:
                            await self._send_discord_pdf_preview_group(
                                session,
                                created_thread_id,
                                group,
                                headers,
                                reply_to_id=created_message_id,
                            )

                    # If we have attachments, send them to the thread (AFTER previews)
                    if files_for_attachments and created_thread_id:
                        await self._send_discord_reply(
                            session,
                            created_thread_id,
                            files_for_attachments,
                            headers,
                            is_thread=True,
                            reply_to_id=created_message_id,
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
            # Use main_embed for first message
            payload = {"embeds": [main_embed]}

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

            async with self._discord_request(session, "POST", message_url, headers=headers, **kwargs) as resp:
                if resp.status in [200, 204]:
                    logger.info(f"[NOTIFIER] Discord Message sent: {notice.title}")
                    resp_data = await resp.json()
                    created_message_id = resp_data.get("id")
                    channel_id = message_url.split("/")[
                        -2
                    ]  # Extract channel ID from URL

                    # --- Send Follow-up Embeds (Split Parts) ---
                    if created_message_id and followup_embeds:
                        # Reply to the first message
                        for idx, f_embed in enumerate(followup_embeds):
                            await self._send_discord_reply_embed(session, channel_id, f_embed, headers, created_message_id)
                    # ------------------------------------------

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

    def _get_embed_length(self, embed: Dict) -> int:
        """Calculate total number of characters in an embed structure."""
        total = 0
        total += len(embed.get("title", ""))
        total += len(embed.get("description", ""))
        total += len(embed.get("footer", {}).get("text", ""))
        total += len(embed.get("author", {}).get("name", ""))
        
        for field in embed.get("fields", []):
            total += len(field.get("name", ""))
            total += len(field.get("value", ""))
        return total

    def _split_embed(self, embed: Dict, max_chars: int = 5800) -> List[Dict]:
        """
        Splits a single large embed into multiple smaller embeds if it exceeds limits.
        Keeps Title/Desc/Author/Footer on the first embed.
        Moves Fields to subsequent embeds if needed.
        """
        total_len = self._get_embed_length(embed)
        if total_len <= max_chars:
            return [embed]

        # It's too big. We need to split fields.
        base_embed = embed.copy()
        all_fields = base_embed.pop("fields", [])
        
        # Calculate base size without fields
        base_len = self._get_embed_length(base_embed)
        
        # First embed receives fields until full
        first_embed = base_embed.copy()
        first_embed["fields"] = []
        current_len = base_len
        
        remaining_fields = []
        
        # Fill first embed
        for field in all_fields:
            field_len = len(field.get("name", "")) + len(field.get("value", ""))
            if current_len + field_len < max_chars:
                first_embed["fields"].append(field)
                current_len += field_len
            else:
                remaining_fields.append(field)

        splitted = [first_embed]
        
        # Create subsequent embeds for remaining fields
        while remaining_fields:
            next_embed = {
                "color": embed.get("color"),
                "footer": {"text": f"{embed.get('footer', {}).get('text', '')} (계속)"},
                "fields": []
            }
            # Add a small title to indicate continuity
            # next_embed["title"] = "..." 
            
            # Start with some overhead
            current_len = len(next_embed["footer"]["text"])
            
            batch_fields = []
            while remaining_fields:
                field = remaining_fields[0]
                field_len = len(field.get("name", "")) + len(field.get("value", ""))
                
                if current_len + field_len < max_chars:
                    batch_fields.append(remaining_fields.pop(0))
                    current_len += field_len
                else:
                    # If a SINGLE field is huge (> 5800), we have a problem. 
                    # But fields are capped at 1024, so this won't happen.
                    # Just break to start next embed
                    if not batch_fields: 
                         # Edge case: First field is somehow too big? shouldn't happen with 1024 limit
                         # Just force it and let it fail or truncate elsewhere
                         batch_fields.append(remaining_fields.pop(0))
                    break
            
            next_embed["fields"] = batch_fields
            splitted.append(next_embed)
            
        return splitted

    async def _send_discord_reply_embed(self, session, channel_id, embed, headers, reply_to_id=None):
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        payload = {"embeds": [embed]}
        if reply_to_id:
            payload["message_reference"] = {"message_id": reply_to_id}
            
        async with self._discord_request(session, "POST", url, headers=headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                 logger.error(f"[NOTIFIER] Failed to send reply embed: {await resp.text()}")

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
            payload = self._discord_reply_payload(reply_to_id)

            self._add_text_part(form, "payload_json", json.dumps(payload))

            for idx, file_info in enumerate(batch):
                field_name = f"files[{idx}]"
                self._add_file_part(
                    form, field_name, file_info["data"], file_info["filename"]
                )

            try:
                async with self._discord_request(session, "POST", url, headers=headers, data=form) as resp:
                    if resp.status not in [200, 201, 204]:
                        logger.error(
                            f"[NOTIFIER] Failed to send reply attachments: {await resp.text()}"
                        )
            except Exception as e:
                logger.error(f"[NOTIFIER] Error sending reply attachments: {e}")

    async def _send_discord_pdf_preview_group(
        self,
        session: aiohttp.ClientSession,
        thread_id: str,
        group: dict,
        headers: dict,
        reply_to_id: Optional[str] = None,
    ):
        """Send a group of Discord PDF preview images as a single message."""
        try:
            message_url = f"https://discord.com/api/v10/channels/{thread_id}/messages"

            # Create caption with PDF filename
            original_filename = group.get("filename", "Preview.pdf")
            caption = f"📑 [미리보기] {original_filename}"

            form = MultipartWriter("form-data")
            payload = self._discord_reply_payload(reply_to_id, content=caption)
            self._add_text_part(form, "payload_json", json.dumps(payload))

            # Add all images in the group
            for idx, img in enumerate(group["images"]):
                self._add_file_part(form, f"files[{idx}]", img["data"], img["filename"])

            async with self._discord_request(session, "POST", message_url, headers=headers, data=form) as resp:
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

    @staticmethod
    def _discord_reply_payload(
        reply_to_id: Optional[str],
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if reply_to_id:
            payload["message_reference"] = {
                "message_id": str(reply_to_id),
                "fail_if_not_exists": False,
            }
            payload["allowed_mentions"] = {"replied_user": False}
        return payload
