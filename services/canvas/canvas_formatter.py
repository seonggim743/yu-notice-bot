"""Plain-text message formatters for Canvas notifications.

Each format_* function returns a single string suitable for both Telegram
(no parse_mode) and Discord (no markdown). Format follows the spec
agreed in the project context — emoji + bracketed course name + body
+ canvas URL on the last line.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from models.canvas import CanvasAnnouncement, CanvasAssignment, CanvasSubmission

_KOREAN_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _strip_html(html: Optional[str], limit: int = 240) -> str:
    """Crude tag strip + whitespace squash + length cap."""
    if not html:
        return ""
    # Remove script/style blocks first so their bodies don't leak through.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _format_kst_datetime(iso: Optional[str]) -> Optional[str]:
    """Render an ISO datetime string as 'M/D(요일) H:MM' in KST.

    Returns None for missing input.
    """
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


def _attachment_lines(attachments: List[Any]) -> List[str]:
    lines = []
    for att in attachments[:3]:
        name = getattr(att, "display_name", "") or "첨부파일"
        size = _format_size(getattr(att, "size", 0) or 0)
        lines.append(f"📎 첨부: {name} ({size})")
    return lines


# ---------- Public format functions ----------


def format_new_assignment(item: CanvasAssignment) -> str:
    course = _course_label(item.course_name)
    lines = [f"📝 [{course}] 새 과제", item.name]

    body = _strip_html(item.description, limit=140)
    if body:
        lines[-1] = f"{item.name} — {body}"

    meta = []
    due = _format_kst_datetime(item.due_at)
    if due:
        meta.append(f"마감: {due}")
    if item.points_possible is not None:
        meta.append(f"배점: {int(item.points_possible) if item.points_possible.is_integer() else item.points_possible}점")
    if meta:
        lines.append(" | ".join(meta))

    if item.submission_types:
        readable = ", ".join(_translate_submission_types(item.submission_types))
        lines.append(f"제출방식: {readable}")

    lines.extend(_attachment_lines(item.attachments))
    if item.html_url:
        lines.append(f"→ {item.html_url}")
    return "\n".join(lines)


def format_modified_assignment(
    item: CanvasAssignment, changes: Dict[str, Any]
) -> str:
    course = _course_label(item.course_name)
    lines = [f"✏️ [{course}] 과제 수정"]

    if "due_at" in changes:
        old = _format_kst_datetime(changes["due_at"].get("old"))
        new = _format_kst_datetime(changes["due_at"].get("new"))
        lines.append(f"{item.name} — 마감일 변경: {old or '없음'} → {new or '없음'}")
    elif "title" in changes:
        old = changes["title"].get("old") or "-"
        lines.append(f"{item.name} — 제목 변경 (이전: {old})")
    elif "body" in changes:
        lines.append(f"{item.name} — 본문 수정됨")
    else:
        lines.append(item.name)

    if item.html_url:
        lines.append(f"→ {item.html_url}")
    return "\n".join(lines)


def format_new_announcement(item: CanvasAnnouncement) -> str:
    course = _course_label(item.course_name)
    lines = [f"📢 [{course}] 강의 공지", item.title]

    body = _strip_html(item.message, limit=240)
    if body:
        lines.append(body)

    lines.extend(_attachment_lines(item.attachments))
    if item.html_url:
        lines.append(f"→ {item.html_url}")
    return "\n".join(lines)


def format_grade_notification(
    submission: CanvasSubmission,
    assignment: Optional[CanvasAssignment] = None,
    course_name: str = "",
) -> str:
    course = _course_label(course_name or (assignment.course_name if assignment else ""))
    lines = [f"📊 [{course}] 성적 등록"]

    name = assignment.name if assignment else f"과제 #{submission.assignment_id}"
    points = assignment.points_possible if assignment else None
    if submission.score is not None and points is not None:
        score_str = f"{_num(submission.score)}/{_num(points)}점"
    elif submission.grade:
        score_str = submission.grade
    elif submission.score is not None:
        score_str = f"{_num(submission.score)}점"
    else:
        score_str = "채점 완료"
    lines.append(f"{name} — {score_str}")

    if assignment and assignment.html_url:
        lines.append(f"→ {assignment.html_url}")
    return "\n".join(lines)


def format_deadline_reminder(
    item: CanvasAssignment, hours_left: int
) -> str:
    course = _course_label(item.course_name)
    if hours_left <= 24:
        label = "D-1" if hours_left > 12 else f"{hours_left}시간 전"
    else:
        days = max(1, round(hours_left / 24))
        label = f"D-{days}"
    lines = [f"⏰ [{course}] 마감 {label}"]

    due = _format_kst_datetime(item.due_at) or "미정"
    if hours_left <= 24:
        lines.append(f"{item.name} — 내일 {due.split(' ', 1)[-1]} 마감" if hours_left > 12 else f"{item.name} — {due} 마감")
    else:
        lines.append(f"{item.name} — {due} 마감")

    status = "미제출 ⚠️" if not item.has_submitted_submissions else "제출 완료"
    lines.append(f"제출 상태: {status}")

    if item.html_url:
        lines.append(f"→ {item.html_url}")
    return "\n".join(lines)


# ---------- Helpers ----------


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


def format_modified_assignment(
    item: CanvasAssignment, changes: Dict[str, Any]
) -> str:
    course = _course_label(item.course_name)
    lines = ["✏️ [{}] 과제 수정".format(course), item.name]

    if "due_at" in changes:
        old = _format_kst_datetime(changes["due_at"].get("old")) or "없음"
        new = _format_kst_datetime(changes["due_at"].get("new")) or "없음"
        lines.append(f"마감: {old} → {new}")
    if "points_possible" in changes:
        old = _format_points(changes["points_possible"].get("old"))
        new = _format_points(changes["points_possible"].get("new"))
        lines.append(f"배점: {old} → {new}")
    if "submission_types" in changes:
        old = _format_submission_types(changes["submission_types"].get("old"))
        new = _format_submission_types(changes["submission_types"].get("new"))
        lines.append(f"제출: {old} → {new}")
    if "title" in changes:
        old = changes["title"].get("old") or "-"
        new = changes["title"].get("new") or "-"
        lines.append(f"제목: {old} → {new}")
    if "body" in changes:
        summary = changes["body"].get("summary") or "본문이 수정되었습니다."
        lines.append(f"본문: {summary}")

    if len(lines) == 2:
        lines.append("변경된 항목이 있습니다.")

    if item.html_url:
        lines.append(f"→ {item.html_url}")
    return "\n".join(lines)
