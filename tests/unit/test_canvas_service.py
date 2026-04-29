from datetime import datetime, timedelta, timezone

import pytest

from models.canvas import (
    CanvasAnnouncement,
    CanvasAssignment,
    CanvasCourse,
    CanvasSubmission,
)
from services.canvas.canvas_service import CanvasService


class FakeRepo:
    def __init__(self):
        self.items = {}
        self.upserts = []
        self.reminder_rows = []
        self.overdue_rows = []
        self.reminder_marks = []
        self.unsubmitted_marks = []

    def get_item(self, canvas_id, item_type):
        return self.items.get((canvas_id, item_type))

    def upsert_item(self, payload):
        item_id = f"{payload['item_type']}:{payload['canvas_id']}"
        stored = dict(payload)
        stored.setdefault("id", item_id)
        self.items[(payload["canvas_id"], payload["item_type"])] = stored
        self.upserts.append(stored)
        return {"id": item_id, "was_inserted": True}

    def get_upcoming_deadlines(self, hours=24):
        return self.reminder_rows

    def mark_reminder_sent(self, item_id, hours_before):
        self.reminder_marks.append((item_id, hours_before))

    def get_recent_overdue_unsubmitted_assignments(self, hours_after_due=1):
        return [
            row
            for row in self.overdue_rows
            if not row.get("alerted_unsubmitted") and not row.get("has_submitted")
        ]

    def mark_unsubmitted_alerted(self, item_id):
        self.unsubmitted_marks.append(item_id)
        for row in self.overdue_rows:
            if row["id"] == item_id:
                row["alerted_unsubmitted"] = True


class FakeClient:
    def __init__(
        self,
        courses=None,
        assignments=None,
        announcements=None,
        submissions=None,
    ):
        self.session = object()
        self.courses = courses or []
        self.assignments = assignments or {}
        self.announcements = announcements or {}
        self.submissions = submissions or {}

    async def get_active_courses(self):
        return self.courses

    async def get_assignments(self, course_id):
        return self.assignments.get(course_id, [])

    async def get_announcements(self, course_ids):
        result = []
        for course_id in course_ids:
            result.extend(self.announcements.get(course_id, []))
        return result

    async def get_submissions(self, course_id):
        return self.submissions.get(course_id, [])


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send_canvas_message(self, session, text, **kwargs):
        self.sent.append({"session": session, "text": text, **kwargs})
        return {"telegram": 1, "discord": "1"}


class FakeAI:
    async def get_diff_summary(self, old_text, new_text):
        return "본문 변경 요약"


def _course():
    return CanvasCourse(id=7, name="논리회로")


def _due(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _row(canvas_id, due_at, submitted=False, reminders=None):
    return {
        "id": f"assignment:{canvas_id}",
        "canvas_id": canvas_id,
        "course_id": 7,
        "course_name": "논리회로",
        "title": f"HW #{canvas_id}",
        "body": "",
        "due_at": due_at,
        "points_possible": 100,
        "has_submitted": submitted,
        "html_url": f"https://canvas.test/assignments/{canvas_id}",
        "reminders_sent": reminders or [],
    }


@pytest.mark.asyncio
async def test_new_assignment_detected_and_dispatched():
    repo = FakeRepo()
    notifier = FakeNotifier()
    assignment = CanvasAssignment(
        id=1,
        course_id=7,
        name="HW #1",
        description="Read chapter 1",
        html_url="https://canvas.test/assignments/1",
    )
    client = FakeClient(courses=[_course()], assignments={7: [assignment]})
    service = CanvasService(repo=repo, notifier=notifier, client=client)

    await service.run()

    assert repo.get_item(1, "assignment")["title"] == "HW #1"
    assert notifier.sent[0]["event_kind"] == "new_assignment"
    assert notifier.sent[0]["title"] == "HW #1"
    assert notifier.sent[0]["url"] == "https://canvas.test/assignments/1"
    assert notifier.sent[0]["is_modified"] is False
    assert "🔗 " not in notifier.sent[0]["text"]


@pytest.mark.asyncio
async def test_assignment_modified_includes_due_body_points_and_submission_diff():
    repo = FakeRepo()
    repo.items[(1, "assignment")] = {
        "id": "assignment:1",
        "canvas_id": 1,
        "item_type": "assignment",
        "course_id": 7,
        "course_name": "논리회로",
        "title": "HW #1",
        "body": "old body",
        "due_at": "2026-04-06T14:59:00Z",
        "points_possible": 50,
        "submission_types": ["online_upload"],
    }
    notifier = FakeNotifier()
    assignment = CanvasAssignment(
        id=1,
        course_id=7,
        course_name="논리회로",
        name="HW #1",
        description="new body",
        due_at="2026-04-08T14:59:00Z",
        points_possible=100,
        submission_types=["online_text_entry"],
        html_url="https://canvas.test/assignments/1",
    )
    client = FakeClient(courses=[_course()], assignments={7: [assignment]})
    service = CanvasService(
        repo=repo,
        notifier=notifier,
        client=client,
        ai_service=FakeAI(),
    )

    await service.run()

    text = notifier.sent[0]["text"]
    assert notifier.sent[0]["event_kind"] == "due_date_changed"
    assert "마감:" in text
    assert "배점:" in text
    assert "제출:" in text
    assert "본문: 본문 변경 요약" in text
    assert notifier.sent[0]["title"] == "HW #1"
    assert notifier.sent[0]["url"] == "https://canvas.test/assignments/1"
    assert notifier.sent[0]["is_modified"] is True


@pytest.mark.asyncio
async def test_new_announcement_detected():
    repo = FakeRepo()
    notifier = FakeNotifier()
    announcement = CanvasAnnouncement(
        id=20,
        course_id=7,
        title="시험 공지",
        message="중간고사 안내",
        html_url="https://canvas.test/announcements/20",
    )
    client = FakeClient(courses=[_course()], announcements={7: [announcement]})
    service = CanvasService(repo=repo, notifier=notifier, client=client)

    await service.run()

    assert repo.get_item(20, "announcement")["title"] == "시험 공지"
    assert notifier.sent[0]["event_kind"] == "new_announcement"
    assert notifier.sent[0]["title"] == "시험 공지"
    assert notifier.sent[0]["url"] == "https://canvas.test/announcements/20"
    assert "🔗 " not in notifier.sent[0]["text"]


@pytest.mark.asyncio
async def test_grade_registered_detected():
    repo = FakeRepo()
    notifier = FakeNotifier()
    submission = CanvasSubmission(
        id=99,
        assignment_id=1,
        course_id=7,
        score=45,
        workflow_state="graded",
    )
    client = FakeClient(courses=[_course()], submissions={7: [submission]})
    service = CanvasService(repo=repo, notifier=notifier, client=client)

    await service.run()

    assert repo.get_item(99, "submission")["score"] == 45
    assert notifier.sent[0]["event_kind"] == "grade_registered"


@pytest.mark.asyncio
async def test_deadline_reminders_choose_72_24_and_3_hour_tiers():
    repo = FakeRepo()
    repo.reminder_rows = [
        _row(1, _due(70)),
        _row(2, _due(23), reminders=[72]),
        _row(3, _due(2), reminders=[72, 24]),
    ]
    notifier = FakeNotifier()
    service = CanvasService(repo=repo, notifier=notifier, client=FakeClient())

    await service.run_reminders()

    assert repo.reminder_marks == [
        ("assignment:1", 72),
        ("assignment:2", 24),
        ("assignment:3", 3),
    ]
    assert [msg["event_kind"] for msg in notifier.sent] == [
        "deadline_reminder",
        "deadline_reminder",
        "deadline_reminder",
    ]


@pytest.mark.asyncio
async def test_submitted_assignment_does_not_send_reminder():
    repo = FakeRepo()
    repo.reminder_rows = [_row(1, _due(2), submitted=True)]
    notifier = FakeNotifier()
    service = CanvasService(repo=repo, notifier=notifier, client=FakeClient())

    await service.run_reminders()

    assert notifier.sent == []
    assert repo.reminder_marks == []


@pytest.mark.asyncio
async def test_unsubmitted_warning_sent_once():
    repo = FakeRepo()
    repo.overdue_rows = [
        {
            **_row(1, _due(-0.5), submitted=False),
            "alerted_unsubmitted": False,
        }
    ]
    notifier = FakeNotifier()
    service = CanvasService(repo=repo, notifier=notifier, client=FakeClient())

    await service.check_unsubmitted()
    await service.check_unsubmitted()

    assert len(notifier.sent) == 1
    assert notifier.sent[0]["event_kind"] == "unsubmitted_warning"
    assert repo.unsubmitted_marks == ["assignment:1"]
