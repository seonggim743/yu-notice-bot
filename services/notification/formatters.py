"""
Message formatting utilities for notifications.
Provides diff generation, emoji mappings, text formatting, and message creation.
"""

import difflib
import html
import re
import textwrap
from datetime import datetime
from typing import Dict, Any, Optional

from bs4 import BeautifulSoup

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
CONTEXT_DIFF_CHARS = 30
CONTEXT_DIFF_GROUP_EQUAL_LIMIT = 6
FORCE_CONTEXT_DIFF_MIN_LINE_LENGTH = 200
FORCE_CONTEXT_DIFF_MIN_RATIO = 0.9
TELEGRAM_QUOTE_LENGTH = 500
DISCORD_QUOTE_LENGTH = 1000
REVISED_BODY_QUOTE_LENGTH = 500
REVISED_BODY_WRAP_WIDTH = 100


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
                old_line = old_lines[idx].strip()
                new_line = new_lines[idx].strip()
                context_lines = _context_diff_lines(
                    old_line, new_line, inline_style
                )
                if context_lines:
                    changes.extend(context_lines)
                else:
                    changes.append(f"🔴 {_format_diff_line(old_line, inline_style)}")
                    changes.append(f"🟢 {_format_diff_line(new_line, inline_style)}")

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


def _context_diff_lines(
    old_line: str, new_line: str, inline_style: Optional[str]
) -> Optional[list[str]]:
    if (
        len(old_line) < INLINE_DIFF_MIN_LINE_LENGTH
        or len(new_line) < INLINE_DIFF_MIN_LINE_LENGTH
    ):
        return None

    matcher = difflib.SequenceMatcher(None, old_line, new_line)
    ratio = matcher.ratio()
    if ratio < INLINE_DIFF_MIN_RATIO:
        return None

    force_context = _should_force_context_diff(old_line, new_line, ratio)
    groups = _context_token_change_groups(old_line, new_line)
    if not groups:
        groups = _context_change_groups(matcher.get_opcodes())
    if not groups:
        return None

    lines = []
    for old_start, old_end, new_start, new_end in groups:
        old_start, old_end = _trim_range(old_line, old_start, old_end)
        new_start, new_end = _trim_range(new_line, new_start, new_end)

        old_segment = old_line[old_start:old_end]
        new_segment = new_line[new_start:new_end]
        if not (
            _has_context_meaningful_span(old_segment, force_context)
            or _has_context_meaningful_span(new_segment, force_context)
        ):
            continue

        context_line = new_line if new_start < new_end else old_line
        context_start = new_start if new_start < new_end else old_start
        context_end = new_end if new_start < new_end else old_end
        before_start = max(0, context_start - CONTEXT_DIFF_CHARS)
        after_end = min(len(context_line), context_end + CONTEXT_DIFF_CHARS)

        before = context_line[before_start:context_start]
        after = context_line[context_end:after_end]
        prefix = "..." if before_start > 0 else ""
        suffix = "..." if after_end < len(context_line) else ""
        lines.append(
            "".join(
                [
                    prefix,
                    _format_diff_line(before, inline_style),
                    _format_context_replacement(
                        old_segment, new_segment, inline_style
                    ),
                    _format_diff_line(after, inline_style),
                    suffix,
                ]
            )
        )

    return lines or None


def _should_force_context_diff(old_line: str, new_line: str, ratio: float) -> bool:
    return (
        "\n" not in old_line
        and "\n" not in new_line
        and len(old_line) >= FORCE_CONTEXT_DIFF_MIN_LINE_LENGTH
        and len(new_line) >= FORCE_CONTEXT_DIFF_MIN_LINE_LENGTH
        and ratio >= FORCE_CONTEXT_DIFF_MIN_RATIO
    )


def _has_context_meaningful_span(text: str, force_context: bool) -> bool:
    if force_context:
        return len(re.sub(r"\s+", "", text)) >= 1
    return _has_meaningful_span(text)


def _context_token_change_groups(
    old_line: str, new_line: str
) -> list[tuple[int, int, int, int]]:
    old_tokens = _token_spans(old_line)
    new_tokens = _token_spans(new_line)
    old_content_tokens = [token for token in old_tokens if token[0].strip()]
    new_content_tokens = [token for token in new_tokens if token[0].strip()]
    if len(old_content_tokens) <= 1 and len(new_content_tokens) <= 1:
        return []

    matcher = difflib.SequenceMatcher(
        None,
        [token for token, _, _ in old_tokens],
        [token for token, _, _ in new_tokens],
    )
    groups = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue

        old_char_start = _token_boundary(old_tokens, old_start, old_line)
        old_char_end = _token_boundary(old_tokens, old_end, old_line)
        new_char_start = _token_boundary(new_tokens, new_start, new_line)
        new_char_end = _token_boundary(new_tokens, new_end, new_line)
        groups.append((old_char_start, old_char_end, new_char_start, new_char_end))
    return groups


def _token_boundary(tokens: list[tuple[str, int, int]], index: int, text: str) -> int:
    if not tokens:
        return 0
    if index <= 0:
        return tokens[0][1]
    if index >= len(tokens):
        return len(text)
    return tokens[index][1]


def _context_change_groups(
    opcodes: list[tuple[str, int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    groups = []
    current = None
    for tag, old_start, old_end, new_start, new_end in opcodes:
        if tag == "equal":
            if (
                current is not None
                and old_end - old_start <= CONTEXT_DIFF_GROUP_EQUAL_LIMIT
                and new_end - new_start <= CONTEXT_DIFF_GROUP_EQUAL_LIMIT
            ):
                current[1] = old_end
                current[3] = new_end
            else:
                if current is not None:
                    groups.append(tuple(current))
                    current = None
            continue

        if current is None:
            current = [old_start, old_end, new_start, new_end]
        else:
            current[1] = old_end
            current[3] = new_end

    if current is not None:
        groups.append(tuple(current))
    return groups


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


def _format_context_replacement(
    old_segment: str, new_segment: str, inline_style: Optional[str]
) -> str:
    old_text = _format_diff_line(old_segment, inline_style)
    new_text = _format_diff_line(new_segment, inline_style)
    if inline_style == "telegram":
        return f"❌<s>{old_text}</s>❌ → ✅<u>{new_text}</u>✅"
    return f"❌{old_text}❌ → ✅{new_text}✅"


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


def strip_html_text(
    raw_text: str, max_length: Optional[int] = None, suffix: str = "..."
) -> str:
    """Remove HTML/media tags and collapse whitespace for notification quotes."""
    if not raw_text:
        return ""

    soup = BeautifulSoup(raw_text, "html.parser")
    for tag in soup(["script", "style", "img"]):
        tag.decompose()
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "tr"]):
        tag.append("\n")

    text = soup.get_text(" ")
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if max_length and len(text) > max_length:
        return text[: max_length - len(suffix)].rstrip() + suffix
    return text


def get_notice_quote_text(
    notice, max_length: int, bullet_summary: bool = False
) -> str:
    """Prefer AI summary, then stripped notice body, for compact quote previews."""
    summary = notice.summary or ""
    is_ai_summary = bool(summary and not summary.startswith("[단신]"))
    source = summary or notice.content or ""
    if summary.startswith("[단신]"):
        source = source.replace("[단신]", "", 1).strip()

    text = strip_html_text(source)
    if is_ai_summary and bullet_summary:
        text = format_summary_lines(text)
    return truncate_text(text, max_length)


def format_revised_body_quote(
    raw_text: str, max_length: Optional[int] = None
) -> str:
    """Format the revised body for compact modified-notice detail replies."""
    text = strip_html_text(raw_text)
    if not text:
        return ""
    text = _break_long_text_by_sentence(text)
    if max_length:
        return truncate_text(text, max_length)
    return text


def split_text_chunks(text: str, max_length: int) -> list[str]:
    """Split text for chat platform limits while preserving line boundaries."""
    if not text:
        return []
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        if len(line) > max_length:
            if current:
                chunks.append("".join(current).rstrip())
                current = []
                current_len = 0
            for idx in range(0, len(line), max_length):
                chunks.append(line[idx : idx + max_length].rstrip())
            continue

        if current_len + len(line) > max_length:
            chunks.append("".join(current).rstrip())
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)

    if current:
        chunks.append("".join(current).rstrip())
    return [chunk for chunk in chunks if chunk]


def format_telegram_revised_body_quote_parts(
    raw_text: str,
    max_length: int = constants.TELEGRAM_MAX_MESSAGE_LENGTH,
) -> list[str]:
    quote = format_revised_body_quote(raw_text)
    if not quote:
        return []

    # Leave room for the title and <pre> wrapper.
    body_limit = max_length - 96
    chunks = split_text_chunks(quote, body_limit)
    total = len(chunks)
    parts = []
    for idx, chunk in enumerate(chunks):
        title = "수정 후 원문" if total == 1 else f"수정 후 원문 ({idx + 1}/{total})"
        escaped = html.escape(chunk, quote=False)
        parts.append(f"📝 <b>{title}</b>\n<pre>{escaped}</pre>")
    return parts


def format_telegram_revised_body_quote(raw_text: str) -> str:
    return "\n\n".join(format_telegram_revised_body_quote_parts(raw_text))


def create_revised_body_quote_field(raw_text: str) -> Optional[Dict[str, Any]]:
    fields = create_revised_body_quote_fields(raw_text)
    return fields[0] if fields else None


def create_revised_body_quote_fields(
    raw_text: str,
    max_length: int = 1000,
) -> list[Dict[str, Any]]:
    quote = format_revised_body_quote(raw_text)
    if not quote:
        return []

    chunks = split_text_chunks(quote, max_length)
    total = len(chunks)
    fields = []
    for idx, chunk in enumerate(chunks):
        name = "📝 수정 후 원문" if total == 1 else f"📝 수정 후 원문 ({idx + 1}/{total})"
        fields.append({"name": name, "value": chunk, "inline": False})
    return fields


def _break_long_text_by_sentence(text: str) -> str:
    if "\n" in text or len(text) <= REVISED_BODY_WRAP_WIDTH:
        return text

    sentences = [part.strip() for part in re.split(r"(?<=\.)\s*", text) if part.strip()]
    if len(sentences) > 1:
        return "\n".join(sentences)

    return "\n".join(
        textwrap.wrap(
            text,
            width=REVISED_BODY_WRAP_WIDTH,
            break_long_words=True,
            replace_whitespace=False,
        )
    )


def format_date(dt_str: str) -> str:
    """Format datetime string to readable format."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def format_change_summary(changes: Dict[str, Any], style: str = "markdown") -> str:
    """
    Format granular changes into a summary string.
    """
    def bold(label: str) -> str:
        if style == "html":
            return f"<b>{label}</b>"
        return f"**{label}**"

    def value(text: Any) -> str:
        text = str(text)
        if style == "html":
            return html.escape(text, quote=False)
        return text

    lines = []
    if "title" in changes:
        lines.append(f"📝 {bold('제목 변경')}: {value(changes['title'])}")
    
    # AI Summary for content
    if "content" in changes:
        lines.append(f"📝 {bold('내용 변경')}: {value(changes['content'])}")
        
    # Granular Attachment Changes
    if "attachments_added" in changes:
        for f in changes["attachments_added"]:
             lines.append(f"➕ {bold('첨부 추가')}: {value(f)}")
    if "attachments_removed" in changes:
        for f in changes["attachments_removed"]:
             lines.append(f"➖ {bold('첨부 삭제')}: {value(f)}")
             
    # Fallback for generic attachment change
    if "attachments" in changes and not ("attachments_added" in changes or "attachments_removed" in changes):
        lines.append(f"📎 {bold('첨부파일 변경')}: {value(changes['attachments'])}")

    if "image" in changes:
        lines.append(f"🖼️ {bold('이미지 변경됨')}")
        
    if "attachment_text" in changes:
        lines.append(f"📎 {bold('첨부파일 내용 변경')}: (상세 내용 확인 필요)")
        
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
    summary_header = "📝 **요약**"
    
    if notice.summary and notice.summary.startswith("[단신]"):
        summary_header = "📝 **원문**"
    
    description_text = ""
    
    # [NEW] Change Summary Header for Modified Notices
    # We now add this as a dedicated Field, so we don't append to description here.
    # However, if we don't have detailed changes but have a reason, we can mention it here or in fields.
    # Logic moved to Field generation.

    quote_text = get_notice_quote_text(
        notice, DISCORD_QUOTE_LENGTH, bullet_summary=True
    )
    if quote_text:
        description_text += f"{summary_header}\n{quote_text}"

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
    if notice.summary and notice.summary.startswith("[단신]"):
        summary_header = "📝 <b>원문</b>"

    msg = f"{prefix} <a href='{notice.url}'><b>{emoji} {safe_title}</b></a>\n\n"

    # [NEW] Change Summary Header
    if not is_new and changes:
        change_summary = format_change_summary(changes, style="html")
        if change_summary:
            msg += f"<b>[변경 요약]</b>\n{change_summary}\n\n"
    elif not is_new and modified_reason:
        msg += f"⚠️ <b>수정 사항</b>: {modified_reason}\n\n"

    quote_text = get_notice_quote_text(
        notice, TELEGRAM_QUOTE_LENGTH, bullet_summary=True
    )
    if quote_text:
        msg += f"{summary_header}\n<blockquote>{escape_html(quote_text)}</blockquote>\n\n"

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
