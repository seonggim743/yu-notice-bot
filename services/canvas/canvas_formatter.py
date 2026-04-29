"""Message formatters for Canvas notifications.

Each `format_*` function returns a single multi-line string. The `html`
flag controls escaping and tag wrapping:

- `html=True`  → Telegram parse_mode="HTML" output. Course/title are wrapped
                 in <b>, body preview in <blockquote>, and any user-supplied
                 strings go through html.escape so an injected `<` cannot
                 break the parse.
- `html=False` → plain text suitable for Discord embed.description.

Body preview length defaults differ per platform: 500 chars for HTML
(Telegram), 1000 chars for plain (Discord). Callers may override.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from models.canvas import CanvasAnnouncement, CanvasAssignment, CanvasSubmission

_KOREAN_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

# Platform-specific defaults for body preview length.
DEFAULT_TELEGRAM_BODY_LIMIT = 500
DEFAULT_DISCORD_BODY_LIMIT = 1000


# ---------- Low-level helpers ----------


def _strip_html(html: Optional[str], limit: int = 500) -> str:
    """Remove tags + collapse whitespace + cap length (with ellipsis)."""
    if not html:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _format_kst_datetime(iso: Optional[str]) -> Optional[str]:
    """Render an ISO datetime string as 'M/D(요일) H:MM' in KST."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    kst = dt.astimezone(timezone(timedelta(hours=9)))
    weekday = _KOREAN_WEEKDAYS[kst.weekday()]
    return f"{kst.month}/{kst.day}({weekday}) {kst.hour}:{kst.minute:02d}"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f}KB"
    return f"{kb / 1024:.1f}MB"


def _course_label(course_name: str) -> str:
    return course_name or "강의"


def _e(text: str, html: bool) -> str:
    """Escape user-supplied text for HTML; leave plain mode untouched."""
    return html_lib.escape(text, quote=False) if html else text


def _b(text: str, html: bool) -> str:
    """Bold wrap for HTML; identity for plain."""
    return f"<b>{_e(text, True)}</b>" if html else text


def _blockquote(text: str, html: bool) -> str:
    """Blockquote wrap for HTML; plain returns the text as-is."""
    if not text:
        return ""
    if html:
        # Preserve newlines inside blockquote — Telegram renders them.
        escaped = _e(text, True)
        return f"<blockquote>{escaped}</blockquote>"
    return text


def _attachment_lines(attachments: List[Any], html: bool) -> List[str]:
    lines = []
    for att in attachments[:3]:
        name = getattr(att, "display_name", "") or "첨부파일"
        size = _format_size(getattr(att, "size", 0) or 0)
        lines.append(f"📎 첨부: {_e(name, html)} ({size})")
    return lines


def _resolve_body_limit(html: bool, override: Optional[int]) -> int:
    if override is not None:
        return override
    return DEFAULT_TELEGRAM_BODY_LIMIT if html else DEFAULT_DISCORD_BODY_LIMIT


# ---------- Public format functions ----------


def format_new_assignment(
    item: CanvasAssignment,
    html: bool = False,
    body_limit: Optional[int] = None,
) -> str:
    course = _course_label(item.course_name)
    limit = _resolve_body_limit(html, body_limit)

    lines = [f"📝 {_b(f'[{course}] 새 과제', html)}", _b(item.name, html)]

    body = _strip_html(item.description, limit=limit)
    if body:
        lines.append(_blockquote(body, html))

    meta = []
    due = _format_kst_datetime(item.due_at)
    if due:
        meta.append(f"⏰ 마감: {due}")
    if item.points_possible is not None:
        meta.append(f"💯 배점: {_num(item.points_possible)}점")
    if meta:
        lines.append(" | ".join(meta))

    if item.submission_types:
        readable = ", ".join(_translate_submission_types(item.submission_types))
        lines.append(f"📎 제출: {_e(readable, html)}")

    lines.extend(_attachment_lines(item.attachments, html))

    return "\n".join(lines)


def format_modified_assignment(
    item: CanvasAssignment,
    changes: Dict[str, Any],
    html: bool = False,
    body_limit: Optional[int] = None,
) -> str:
    course = _course_label(item.course_name)
    lines = [f"✏️ {_b(f'[{course}] 과제 수정', html)}", _b(item.name, html)]

    if "due_at" in changes:
        old = _format_kst_datetime(changes["due_at"].get("old")) or "없음"
        new = _format_kst_datetime(changes["due_at"].get("new")) or "없음"
        lines.append(f"⏰ 마감: {_e(old, html)} → {_e(new, html)}")
    if "points_possible" in changes:
        old = _format_points(changes["points_possible"].get("old"))
        new = _format_points(changes["points_possible"].get("new"))
        lines.append(f"💯 배점: {_e(old, html)} → {_e(new, html)}")
    if "submission_types" in changes:
        old = _format_submission_types(changes["submission_types"].get("old"))
        new = _format_submission_types(changes["submission_types"].get("new"))
        lines.append(f"📎 제출: {_e(old, html)} → {_e(new, html)}")
    if "title" in changes:
        old = changes["title"].get("old") or "-"
        new = changes["title"].get("new") or "-"
        lines.append(f"📝 제목: {_e(str(old), html)} → {_e(str(new), html)}")
    if "body" in changes:
        summary = changes["body"].get("summary") or "본문이 수정되었습니다."
        lines.append(f"본문: {_e(summary, html)}")

    if len(lines) == 2:
        lines.append(_e("변경된 항목이 있습니다.", html))

    return "\n".join(lines)


def format_unsubmitted_warning(
    item: CanvasAssignment, html: bool = False
) -> str:
    course = _course_label(item.course_name)
    lines = [f"⚠️ {_b(f'[{course}] 미제출 과제', html)}", _b(item.name, html)]

    due = _format_kst_datetime(item.due_at) or "미정"
    lines.append(f"⏰ 마감 지남 ({_e(due, html)})")

    return "\n".join(lines)


def format_new_announcement(
    item: CanvasAnnouncement,
    html: bool = False,
    body_limit: Optional[int] = None,
) -> str:
    course = _course_label(item.course_name)
    limit = _resolve_body_limit(html, body_limit)

    lines = [f"📢 {_b(f'[{course}] 강의 공지', html)}", _b(item.title, html)]

    body = _strip_html(item.message, limit=limit)
    if body:
        lines.append(_blockquote(body, html))

    lines.extend(_attachment_lines(item.attachments, html))

    return "\n".join(lines)


def format_grade_notification(
    submission: CanvasSubmission,
    assignment: Optional[CanvasAssignment] = None,
    course_name: str = "",
    html: bool = False,
) -> str:
    course = _course_label(
        course_name or (assignment.course_name if assignment else "")
    )
    lines = [f"📊 {_b(f'[{course}] 성적 등록', html)}"]

    name = assignment.name if assignment else f"과제 #{submission.assignment_id}"
    lines.append(_b(name, html))

    points = assignment.points_possible if assignment else None
    if submission.score is not None and points is not None:
        score_str = f"{_num(submission.score)}/{_num(points)}점"
    elif submission.grade:
        score_str = submission.grade
    elif submission.score is not None:
        score_str = f"{_num(submission.score)}점"
    else:
        score_str = "채점 완료"
    lines.append(f"💯 {_e(score_str, html)}")

    return "\n".join(lines)


def format_deadline_reminder(
    item: CanvasAssignment,
    hours_left: int,
    html: bool = False,
) -> str:
    course = _course_label(item.course_name)
    if hours_left <= 24:
        label = "D-1" if hours_left > 12 else f"{hours_left}시간 전"
    else:
        days = max(1, round(hours_left / 24))
        label = f"D-{days}"
    lines = [f"⏰ {_b(f'[{course}] 마감 {label}', html)}", _b(item.name, html)]

    due = _format_kst_datetime(item.due_at) or "미정"
    if hours_left <= 24 and hours_left > 12:
        # "내일 23:59 마감"
        time_part = due.split(" ", 1)[-1]
        lines.append(f"⏰ 내일 {_e(time_part, html)} 마감")
    else:
        lines.append(f"⏰ {_e(due, html)} 마감")

    if not item.has_submitted_submissions:
        lines.append("제출 상태: 미제출 ⚠️")
    else:
        lines.append("제출 상태: 제출 완료")

    return "\n".join(lines)


# ---------- Misc helpers ----------


def _num(value) -> str:
    """Render a numeric score without trailing .0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"


_SUBMISSION_TYPE_KOREAN = {
    "online_upload": "파일 업로드",
    "online_text_entry": "텍스트 입력",
    "online_url": "URL 제출",
    "media_recording": "미디어 녹화",
    "online_quiz": "온라인 퀴즈",
    "external_tool": "외부 도구",
    "discussion_topic": "토론 답변",
    "on_paper": "오프라인",
    "none": "제출 없음",
}


def _translate_submission_types(types: List[str]) -> List[str]:
    return [_SUBMISSION_TYPE_KOREAN.get(t, t) for t in types]


def _format_points(value: Any) -> str:
    if value is None or value == "":
        return "없음"
    return f"{_num(value)}점"


def _format_submission_types(value: Any) -> str:
    if not value:
        return "없음"
    if isinstance(value, str):
        value = [value]
    return ", ".join(_translate_submission_types(list(value)))
