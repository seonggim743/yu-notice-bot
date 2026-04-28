"""Tests for canvas_formatter pure-function output.

The formatter has two render modes: plain (Discord embed) and
html=True (Telegram parse_mode=HTML). Both produce the same logical
layout — emoji + course/title + body + meta + 🔗 url — but differ in
markup.
"""
import pytest

from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
    CanvasAttachment,
    CanvasSubmission,
)
from services.canvas import canvas_formatter as fmt


def test_strip_html_removes_tags_and_collapses_whitespace():
    raw = "<p>Hello   <b>world</b>!</p>\n\n<script>alert(1)</script>"
    out = fmt._strip_html(raw)
    assert "Hello" in out and "world!" in out
    assert "<b>" not in out and "alert" not in out


def test_strip_html_truncates_to_limit():
    out = fmt._strip_html("a" * 1500, limit=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_format_new_assignment_plain_layout():
    a = CanvasAssignment(
        id=1,
        course_id=2,
        course_name="논리회로",
        name="HW #5",
        description="<p>5.3, 5.5</p>",
        due_at="2026-04-20T14:59:00Z",  # 23:59 KST
        points_possible=100.0,
        submission_types=["online_upload"],
        html_url="https://canvas.yu.ac.kr/courses/2/assignments/1",
    )
    text = fmt.format_new_assignment(a, html=False)
    lines = text.splitlines()
    assert lines[0] == "📝 [논리회로] 새 과제"
    assert lines[1] == "HW #5"
    assert "5.3, 5.5" in text
    assert any("배점: 100점" in line for line in lines)
    assert any("📎 제출:" in line and "파일 업로드" in line for line in lines)
    assert lines[-1] == "🔗 https://canvas.yu.ac.kr/courses/2/assignments/1"


def test_format_new_assignment_html_wraps_bold_and_blockquote():
    a = CanvasAssignment(
        id=1,
        course_id=2,
        course_name="논리회로",
        name="HW #5",
        description="<p>5.3, 5.5</p>",
        html_url="https://canvas.yu.ac.kr/courses/2/assignments/1",
    )
    text = fmt.format_new_assignment(a, html=True)
    assert "<b>[논리회로] 새 과제</b>" in text
    assert "<b>HW #5</b>" in text
    assert "<blockquote>" in text and "5.3, 5.5" in text
    assert "🔗 https://canvas.yu.ac.kr/courses/2/assignments/1" in text


def test_format_html_escapes_user_supplied_text():
    a = CanvasAssignment(
        id=1,
        course_id=2,
        course_name="C&Programming",  # ampersand must be escaped
        name="HW <script>",
        html_url="https://canvas.yu.ac.kr/x",
    )
    text = fmt.format_new_assignment(a, html=True)
    assert "C&amp;Programming" in text
    assert "&lt;script&gt;" in text
    # Make sure raw <script> doesn't survive
    assert "<script>" not in text


def test_format_modified_assignment_due_date_change():
    a = CanvasAssignment(
        id=3,
        course_id=2,
        course_name="논리회로",
        name="HW #3",
        html_url="https://canvas.yu.ac.kr/courses/2/assignments/3",
    )
    changes = {
        "due_at": {
            "old": "2026-04-06T14:59:00Z",
            "new": "2026-04-08T14:59:00Z",
        }
    }
    text = fmt.format_modified_assignment(a, changes, html=False)
    assert text.startswith("✏️ [논리회로] 과제 수정")
    assert "마감:" in text and "→" in text
    assert "🔗 https://canvas.yu.ac.kr/courses/2/assignments/3" in text


def test_format_new_announcement_with_attachment():
    a = CanvasAnnouncement(
        id=10,
        course_id=2,
        course_name="논리회로",
        title="중간고사 공지",
        message="<p>일시: 4/23 15:10</p>",
        attachments=[
            CanvasAttachment(display_name="ch5.pdf", **{"content-type": "application/pdf"}, size=160_000)
        ],
        html_url="https://canvas.yu.ac.kr/courses/2/discussion_topics/10",
    )
    text = fmt.format_new_announcement(a)
    assert text.startswith("📢 [논리회로] 강의 공지")
    assert "중간고사 공지" in text
    assert "📎 첨부: ch5.pdf" in text
    assert "KB" in text


def test_format_grade_notification_uses_assignment_score_total():
    a = CanvasAssignment(
        id=3,
        course_id=2,
        course_name="논리회로",
        name="HW #3",
        points_possible=50.0,
        html_url="https://canvas.yu.ac.kr/courses/2/assignments/3",
    )
    sub = CanvasSubmission(
        id=99, assignment_id=3, course_id=2, score=45.0, workflow_state="graded"
    )
    text = fmt.format_grade_notification(sub, assignment=a)
    assert text.startswith("📊 [논리회로] 성적 등록")
    assert "HW #3" in text
    assert "45/50점" in text


def test_format_deadline_reminder_unsubmitted():
    a = CanvasAssignment(
        id=5,
        course_id=2,
        course_name="논리회로",
        name="HW #5",
        due_at="2026-04-29T14:59:00Z",
        has_submitted_submissions=False,
        html_url="https://canvas.yu.ac.kr/courses/2/assignments/5",
    )
    text = fmt.format_deadline_reminder(a, hours_left=24)
    assert text.startswith("⏰ [논리회로] 마감")
    assert "미제출" in text
    assert "🔗 " in text
