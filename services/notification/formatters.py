"""
Message formatting utilities for notifications.
Provides diff generation, emoji mappings, text formatting, and message creation.
"""

import difflib
import html
import re
from datetime import datetime
from typing import Dict, Any, Optional

from core import constants
from core.utils import get_utc_now

# Re-export from constants for backward compatibility and convenience
CATEGORY_EMOJIS = constants.CATEGORY_EMOJIS
CATEGORY_COLORS = constants.CATEGORY_COLORS
CATEGORY_ICONS = constants.CATEGORY_ICON_URLS
FILE_EXTENSION_EMOJIS = constants.FILE_EMOJI_MAP
SITE_NAME_MAP = constants.SITE_NAME_MAP
SCHOOL_LOGO_URL = constants.SCHOOL_LOGO_URL


INLINE_DIFF_MIN_LINE_LENGTH = 30
INLINE_DIFF_MIN_RATIO = 0.45
INLINE_DIFF_MIN_SPAN = 2


def generate_clean_diff(
    old_text: str, new_text: str, inline_style: Optional[str] = None
) -> str:
    """
    Generates a clean, line-by-line diff showing only changes.

    Args:
        old_text: Original text
        new_text: New text

    Returns:
        Formatted diff string with 🔴 (removed) and 🟢 (added) indicators
    """
    if not old_text or not new_text:
        return ""

    if inline_style not in {None, "telegram", "discord"}:
        inline_style = None

    changes = []
    matcher = difflib.SequenceMatcher(
        None, old_text.splitlines(), new_text.splitlines()
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue

        old_lines = old_text.splitlines()[old_start:old_end]
        new_lines = new_text.splitlines()[new_start:new_end]

        if tag == "replace":
            paired = min(len(old_lines), len(new_lines))
            for idx in range(paired):
                old_line, new_line = _highlight_line_pair(
                    old_lines[idx].strip(), new_lines[idx].strip(), inline_style
                )
                changes.append(f"🔴 {old_line}")
                changes.append(f"🟢 {new_line}")

            for line in old_lines[paired:]:
                changes.append(f"🔴 {_format_diff_line(line.strip(), inline_style)}")
            for line in new_lines[paired:]:
                changes.append(f"🟢 {_format_diff_line(line.strip(), inline_style)}")
        elif tag == "delete":
            for line in old_lines:
                changes.append(f"🔴 {_format_diff_line(line.strip(), inline_style)}")
        elif tag == "insert":
            for line in new_lines:
                changes.append(f"🟢 {_format_diff_line(line.strip(), inline_style)}")

    # Return full result without truncation
    return "\n".join(changes)


def _highlight_line_pair(
    old_line: str, new_line: str, inline_style: Optional[str]
) -> tuple[str, str]:
    if not inline_style:
        return old_line, new_line

    if (
        len(old_line) < INLINE_DIFF_MIN_LINE_LENGTH
        or len(new_line) < INLINE_DIFF_MIN_LINE_LENGTH
    ):
        return (
            _format_diff_line(old_line, inline_style),
            _format_diff_line(new_line, inline_style),
        )

    matcher = difflib.SequenceMatcher(None, old_line, new_line)
    if matcher.ratio() < INLINE_DIFF_MIN_RATIO:
        return (
            _format_diff_line(old_line, inline_style),
            _format_diff_line(new_line, inline_style),
        )

    old_ranges, new_ranges = _changed_token_ranges(old_line, new_line)
    if not old_ranges and not new_ranges:
        old_ranges = []
        new_ranges = []
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag in {"replace", "delete"} and _has_meaningful_span(
                old_line[old_start:old_end]
            ):
                old_ranges.append((old_start, old_end))
            if tag in {"replace", "insert"} and _has_meaningful_span(
                new_line[new_start:new_end]
            ):
                new_ranges.append((new_start, new_end))

    if not old_ranges and not new_ranges:
        return (
            _format_diff_line(old_line, inline_style),
            _format_diff_line(new_line, inline_style),
        )

    return (
        _apply_inline_ranges(old_line, old_ranges, inline_style),
        _apply_inline_ranges(new_line, new_ranges, inline_style),
    )


def _changed_token_ranges(
    old_line: str, new_line: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    old_tokens = _token_spans(old_line)
    new_tokens = _token_spans(new_line)
    if not old_tokens or not new_tokens:
        return [], []

    matcher = difflib.SequenceMatcher(
        None,
        [token for token, _, _ in old_tokens],
        [token for token, _, _ in new_tokens],
    )
    old_ranges = []
    new_ranges = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag in {"replace", "delete"} and old_start < old_end:
            start = old_tokens[old_start][1]
            end = old_tokens[old_end - 1][2]
            start, end = _trim_range(old_line, start, end)
            if _has_meaningful_span(old_line[start:end]):
                old_ranges.append((start, end))
        if tag in {"replace", "insert"} and new_start < new_end:
            start = new_tokens[new_start][1]
            end = new_tokens[new_end - 1][2]
            start, end = _trim_range(new_line, start, end)
            if _has_meaningful_span(new_line[start:end]):
                new_ranges.append((start, end))
    return old_ranges, new_ranges


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    return [
        (m.group(0), m.start(), m.end())
        for m in re.finditer(r"\s+|[^\s]+", text)
    ]


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _has_meaningful_span(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    return len(normalized) >= INLINE_DIFF_MIN_SPAN


def _apply_inline_ranges(
    text: str, ranges: list[tuple[int, int]], inline_style: str
) -> str:
    if not ranges:
        return _format_diff_line(text, inline_style)

    parts = []
    cursor = 0
    for start, end in _merge_ranges(ranges):
        if start > cursor:
            parts.append(_format_diff_line(text[cursor:start], inline_style))
        changed = _format_diff_line(text[start:end], inline_style)
        if inline_style == "telegram":
            parts.append(f"<u>{changed}</u>")
        else:
            parts.append(f"**{changed}**")
        cursor = end
    if cursor < len(text):
        parts.append(_format_diff_line(text[cursor:], inline_style))
    return "".join(parts)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _format_diff_line(text: str, inline_style: Optional[str]) -> str:
    if inline_style == "telegram":
        return html.escape(text, quote=False)
    return text


def get_category_emoji(category: str) -> str:
    """Get emoji for category."""
    return CATEGORY_EMOJIS.get(category, "📢")


def get_category_color(category: str) -> int:
    """Get color code for category (Discord Embed)."""
    return CATEGORY_COLORS.get(category, 0x95A5A6)


def get_category_icon_url(category: str) -> str:
    """Get icon URL for category (Discord Thumbnail)."""
    return CATEGORY_ICONS.get(category, CATEGORY_ICONS["일반"])


def get_file_emoji(filename: str) -> str:
    """Get emoji for file based on extension."""
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    return FILE_EXTENSION_EMOJIS.get(ext, "📄")


def get_site_name(site_key: str) -> str:
    """Get localized site name."""
    return SITE_NAME_MAP.get(site_key, site_key)


def format_summary_lines(summary: str) -> str:
    """
    Format summary text to ensure every line starts with a hyphen.

    Args:
        summary: Raw summary text

    Returns:
        Formatted summary with hyphens
    """
    lines = summary.split("\n")
    formatted_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("-"):
            line = f"- {line}"
        formatted_lines.append(line)
    return "\n".join(formatted_lines)


def escape_html(text: str) -> str:
    """HTML escape for safe display."""
    return html.escape(text)


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to max length with suffix."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def format_date(dt_str: str) -> str:
    """Format datetime string to readable format."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def format_change_summary(changes: Dict[str, Any]) -> str:
    """
    Format granular changes into a summary string.
    """
    lines = []
    if "title" in changes:
        lines.append(f"📝 **제목 변경**: {changes['title']}")
    
    # AI Summary for content
    if "content" in changes:
        lines.append(f"📝 **내용 변경**: {changes['content']}")
        
    # Granular Attachment Changes
    if "attachments_added" in changes:
        for f in changes["attachments_added"]:
             lines.append(f"➕ **첨부 추가**: {f}")
    if "attachments_removed" in changes:
        for f in changes["attachments_removed"]:
             lines.append(f"➖ **첨부 삭제**: {f}")
             
    # Fallback for generic attachment change
    if "attachments" in changes and not ("attachments_added" in changes or "attachments_removed" in changes):
        lines.append(f"📎 **첨부파일 변경**: {changes['attachments']}")

    if "image" in changes:
        lines.append("🖼️ **이미지 변경됨**")
        
    if "attachment_text" in changes:
        lines.append(f"📎 **첨부파일 내용 변경**: (상세 내용 확인 필요)")
        
    return "\n".join(lines)


def create_discord_embed(notice, is_new: bool, modified_reason: str = "", changes: Optional[Dict] = None) -> dict:
    """
    Create Discord Embed with consistent formatting.

    Args:
        notice: Notice object
        is_new: Whether this is a new notice
        modified_reason: Reason for modification (if applicable)
        changes: Dictionary of specific changes (optional)

    Returns:
        Discord Embed dict
    """
    emoji = get_category_emoji(notice.category)
    color = get_category_color(notice.category)
    prefix = "🆕" if is_new else "🔄"

    # Build footer text
    footer_parts = []
    if notice.author:
        footer_parts.append(notice.author)
    if notice.published_at:
        footer_parts.append(f"작성일: {notice.published_at.strftime('%Y.%m.%d %H:%M')}")
    else:
        footer_parts.append(get_site_name(notice.site_key))

    footer_text = (
        " • ".join(footer_parts) if footer_parts else get_site_name(notice.site_key)
    )

    # Handle Short Article (단신)
    summary_text = notice.summary
    summary_header = "📝 **요약**"
    
    if notice.summary and notice.summary.startswith("[단신]"):
        summary_text = notice.summary.replace("[단신]", "").strip()
        summary_header = "📝 **원문**"
    
    description_text = ""
    
    # [NEW] Change Summary Header for Modified Notices
    # We now add this as a dedicated Field, so we don't append to description here.
    # However, if we don't have detailed changes but have a reason, we can mention it here or in fields.
    # Logic moved to Field generation.

    if summary_text:
        formatted_summary = format_summary_lines(summary_text)
        description_text += f"{summary_header}\n{formatted_summary}"

    embed = {
        "title": f"{prefix} {emoji} {truncate_text(notice.title, 200)}",
        "description": truncate_text(description_text, 4000), # Truncate description
        "color": color,
        "url": notice.url,
        "author": {"name": "Yu Notice Bot", "icon_url": SCHOOL_LOGO_URL},
        "footer": {"text": footer_text},
        "timestamp": get_utc_now().isoformat(),
        "fields": [],
    }

    # Add thumbnail (category icon or notice image)
    if notice.image_urls:
        # Use first image as main image (not thumbnail)
        pass  # Will be handled separately as attachment
    else:
        embed["thumbnail"] = {"url": get_category_icon_url(notice.category)}

    # Add fields - Skip for dormitory_menu
    is_menu = notice.site_key == "dormitory_menu"

    # [NEW] Add Change Summary as the FIRST Field
    if not is_new:
        change_summary = ""
        if changes:
             change_summary = format_change_summary(changes)
        
        if change_summary:
            embed["fields"].append(
                {"name": "🔄 변경 요약", "value": change_summary, "inline": False}
            )
        elif modified_reason:
             # Fallback if no specific changes dict or empty summary
             embed["fields"].append(
                {"name": "⚠️ 수정 사항", "value": truncate_text(modified_reason, 1000), "inline": False}
            )

    if notice.deadline:
        embed["fields"].append(
            {"name": "📅 마감일", "value": notice.deadline, "inline": True}
        )

    if not is_menu and notice.target_dept and notice.target_dept != "전체":
        embed["fields"].append(
            {"name": "🎯 대상", "value": truncate_text(notice.target_dept, 1000), "inline": True}
        )

    if not is_menu and notice.eligibility:
        eligibility_text = notice.eligibility
        if isinstance(notice.eligibility, list):
            eligibility_text = ", ".join(notice.eligibility)

        if eligibility_text:
            embed["fields"].append(
                {"name": "✅ 자격 요건", "value": truncate_text(eligibility_text, 1000), "inline": False}
            )

    if notice.tags and len(notice.tags) > 0:
        tags_text = " ".join([f"`{tag}`" for tag in notice.tags[:5]])
        embed["fields"].append({"name": "🏷️ 태그", "value": tags_text, "inline": False})



    return embed


def create_telegram_message(notice, is_new: bool, modified_reason: str = "", changes: Optional[Dict] = None) -> str:
    """
    Create Telegram message with consistent formatting.

    Args:
        notice: Notice object
        is_new: Whether this is a new notice
        modified_reason: Reason for modification (if applicable)
        changes: Dictionary of specific changes (optional)

    Returns:
        Telegram message HTML string
    """
    emoji = get_category_emoji(notice.category)
    prefix = "🆕" if is_new else "🔄"

    safe_title = escape_html(notice.title)
    
    # Handle Short Article (단신)
    summary_header = "📝 <b>요약</b>"
    summary_text = notice.summary
    
    if notice.summary and notice.summary.startswith("[단신]"):
        summary_header = "📝 <b>원문</b>"
        summary_text = notice.summary.replace("[단신]", "").strip()
    
    safe_summary = (
        format_summary_lines(escape_html(summary_text)) if summary_text else ""
    )

    msg = f"{prefix} <a href='{notice.url}'><b>{emoji} {safe_title}</b></a>\n\n"

    # [NEW] Change Summary Header
    if not is_new and changes:
        change_summary = format_change_summary(changes)
        if change_summary:
            msg += f"<b>[변경 요약]</b>\n{change_summary}\n\n"
    elif not is_new and modified_reason:
        msg += f"⚠️ <b>수정 사항</b>: {modified_reason}\n\n"

    msg += f"{summary_header}\n{safe_summary}\n\n"

    # Add optional fields - Skip for dormitory_menu
    is_menu = notice.site_key == "dormitory_menu"

    if notice.author:
        msg += f"✍️ <b>작성자</b>: {escape_html(notice.author)}\n"

    if notice.published_at:
        msg += f"📅 <b>작성일</b>: {notice.published_at.strftime('%Y.%m.%d %H:%M')}\n"

    if notice.deadline:
        msg += f"⏰ <b>마감일</b>: {notice.deadline}\n"

    if not is_menu and notice.target_dept and notice.target_dept != "전체":
        msg += f"🎯 <b>대상</b>: {escape_html(notice.target_dept)}\n"

    if not is_menu and notice.eligibility:
        items = notice.eligibility[:3]
        reqs = "\n".join([f"• {escape_html(req)}" for req in items])
        msg += f"✅ <b>자격요건</b>\n{reqs}\n\n"
    elif notice.deadline or (not is_menu and notice.target_dept):
        msg += "\n"

    # Removed generic modified_reason field in favor of header
    if modified_reason and not changes:
        msg += f"⚠️ <b>수정 사항</b>: {modified_reason}\n\n"

    # Hashtags
    tags = []
    if notice.tags:
        tags = [
            f"#{tag.replace(' ', '_').replace('/', '_')}" for tag in notice.tags[:5]
        ]
    else:
        tags = [f"#{notice.category}"]

    tags.append(f"#{get_site_name(notice.site_key)}")
    msg += " ".join(tags)

    return msg
