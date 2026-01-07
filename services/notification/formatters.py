"""
Message formatting utilities for notifications.
Provides diff generation, emoji mappings, text formatting, and message creation.
"""

import difflib
import html
from datetime import datetime
from typing import Dict, Any, Optional


# Category Emoji Mappings
CATEGORY_EMOJIS = {
    "긴급": "🚨",
    "장학": "💰",
    "학사": "🎓",
    "취업": "💼",
    "행사": "🎉",
    "과제/시험": "📝",
    "수상/성과": "🏆",
    "생활관": "🏠",
    "일반": "📢",
}

# Category Color Mappings (for Discord Embeds)
CATEGORY_COLORS = {
    "긴급": 0xFF0000,  # 🔴 Red
    "장학": 0xFFD700,  # 💰 Gold
    "학사": 0x0099FF,  # 🎓 Blue
    "취업": 0x9B59B6,  # 💼 Purple
    "행사": 0x2ECC71,  # 🎉 Green
    "과제/시험": 0xE74C3C,  # 📝 Red-Orange
    "수상/성과": 0xF39C12,  # 🏆 Orange
    "생활관": 0x1ABC9C,  # 🏠 Turquoise
    "일반": 0x95A5A6,  # 📢 Grey
}

# Category Icon URLs (for Discord Thumbnails)
CATEGORY_ICONS = {
    "긴급": "https://cdn-icons-png.flaticon.com/512/595/595067.png",
    "장학": "https://cdn-icons-png.flaticon.com/512/3135/3135706.png",
    "학사": "https://cdn-icons-png.flaticon.com/512/3976/3976625.png",
    "취업": "https://cdn-icons-png.flaticon.com/512/3281/3281307.png",
    "행사": "https://cdn-icons-png.flaticon.com/512/3176/3176366.png",
    "과제/시험": "https://cdn-icons-png.flaticon.com/512/2965/2965358.png",
    "생활관": "https://cdn-icons-png.flaticon.com/512/1946/1946488.png",
    "일반": "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png",
}

# File Extension Emoji Mappings
FILE_EXTENSION_EMOJIS = {
    "pdf": "📕",
    "doc": "📘",
    "docx": "📘",
    "xls": "📗",
    "xlsx": "📗",
    "ppt": "📙",
    "pptx": "📙",
    "zip": "📦",
    "rar": "📦",
    "hwp": "📄",
    "hwpx": "📄",
    "jpg": "🖼️",
    "jpeg": "🖼️",
    "png": "🖼️",
    "gif": "🖼️",
}

# Site Name Mappings (Localization)
SITE_NAME_MAP = {
    "yu_news": "영대소식",
    "cse_notice": "컴공공지",
    "bachelor_guide": "학사안내",
    "calendar": "학사일정",
    "dormitory_notice": "생활관공지",
    "dormitory_menu": "기숙사식단",
}

# School Logo URL
SCHOOL_LOGO_URL = "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png"


def generate_clean_diff(old_text: str, new_text: str) -> str:
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

    d = difflib.Differ()
    diff = list(d.compare(old_text.splitlines(), new_text.splitlines()))

    changes = []
    for line in diff:
        if line.startswith("- "):
            changes.append(f"🔴 {line[2:].strip()}")
        elif line.startswith("+ "):
            changes.append(f"🟢 {line[2:].strip()}")
        elif line.startswith("? "):
            continue

    # Return full result without truncation
    return "\n".join(changes)


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
        "timestamp": datetime.utcnow().isoformat(),
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
