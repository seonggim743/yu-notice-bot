"""
Discord notification service.
"""
import aiohttp
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any

from aiohttp import MultipartWriter

from core.config import settings
from core.logger import get_logger
from core import constants
from models.notice import Notice
from services.notification.base import BaseNotifier
from services.notification.formatters import create_discord_embed, format_change_summary
from services.tag_matcher import TagMatcher

logger = get_logger(__name__)


class DiscordNotifier(BaseNotifier):
    """Handles all Discord-specific notification logic."""

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

                        # --- Send Follow-up Embeds (Split Parts) ---
                        if hasattr(self, "_send_discord_reply") and created_thread_id and followup_embeds:
                            for idx, f_embed in enumerate(followup_embeds):
                                try:
                                    # Send as simple message with embed
                                    f_payload = {"embeds": [f_embed]}
                                    f_url = f"https://discord.com/api/v10/channels/{created_thread_id}/messages"
                                    async with session.post(f_url, headers=headers, json=f_payload) as f_resp:
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

                async with session.post(message_url, headers=headers, **kwargs) as resp:
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
            
        async with session.post(url, headers=headers, json=payload) as resp:
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
