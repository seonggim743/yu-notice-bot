"""Pydantic models for Canvas LMS API responses.

Each model normalizes the subset of Canvas fields the bot actually uses;
extra fields from the API are ignored. Datetimes from Canvas come as ISO
strings — we keep them as strings here and parse to datetime only when
needed (e.g. due-date arithmetic) to avoid timezone surprises.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class CanvasAttachment(BaseModel):
    """Attachment metadata embedded in announcements / assignments."""

    display_name: str = ""
    url: str = ""
    size: int = 0
    content_type: str = Field("", alias="content-type")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class CanvasCourse(BaseModel):
    id: int
    name: str = ""
    course_code: str = ""
    enrollment_term_id: Optional[int] = None

    model_config = {"extra": "ignore"}


class CanvasAssignment(BaseModel):
    id: int
    course_id: int
    course_name: str = ""  # injected by CanvasService, not from API
    name: str = ""
    description: str = ""  # HTML body
    due_at: Optional[str] = None
    unlock_at: Optional[str] = None
    lock_at: Optional[str] = None
    points_possible: Optional[float] = None
    submission_types: List[str] = Field(default_factory=list)
    has_submitted_submissions: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    html_url: str = ""
    attachments: List[CanvasAttachment] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class CanvasAnnouncement(BaseModel):
    id: int
    course_id: int = 0  # parsed from context_code by CanvasService
    course_name: str = ""
    title: str = ""
    message: str = ""  # HTML body
    created_at: Optional[str] = None
    posted_at: Optional[str] = None
    attachments: List[CanvasAttachment] = Field(default_factory=list)
    html_url: str = ""
    context_code: str = ""

    model_config = {"extra": "ignore"}


class CanvasSubmission(BaseModel):
    id: int
    assignment_id: int
    course_id: int = 0  # injected by CanvasService
    score: Optional[float] = None
    grade: Optional[str] = None
    graded_at: Optional[str] = None
    workflow_state: str = "unsubmitted"  # unsubmitted | submitted | graded | pending_review

    model_config = {"extra": "ignore"}
