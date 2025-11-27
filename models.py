from pydantic import BaseModel, Field, HttpUrl, validator
from typing import List, Optional, Dict, Any
from datetime import date, datetime

class Attachment(BaseModel):
    text: str
    url: str

class NoticeItem(BaseModel):
    id: str
    title: str
    link: str
    attachments: List[Attachment] = []
    image_url: Optional[str] = None
    category: str = "일반"
    summary: Optional[str] = None
    is_exam: bool = False
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    target_grades: List[int] = []
    target_dept: Optional[str] = None

class TargetConfig(BaseModel):
    key: str
    name: str
    url: str
    base_url: str
    content_selector: str
    list_selector: str = "table tbody tr"
    title_selector: str = "a"
    link_selector: str = "a"
    type: Optional[str] = None # 'calendar', 'menu', or None

class BotConfig(BaseModel):
    topic_map: Dict[str, int]
    targets: List[TargetConfig]
    keywords: List[str] = []
    user_agent: str
    ai_prompt_template: str
    calendar_prompt_template: str
    menu_prompt_template: str

class ScraperState(BaseModel):
    last_calendar_check_morning: Optional[str] = None
    last_calendar_check_evening: Optional[str] = None
    last_weekly_briefing: Optional[str] = None
    last_daily_summary: Optional[str] = None
    last_pinned_menu_id: Optional[int] = None
    pinned_exams: List[Dict[str, Any]] = []
    daily_notices_buffer: Optional[Dict[str, Any]] = {}
    daily_notices_buffer: Optional[Dict[str, Any]] = {}
    last_daily_menu_check: Optional[str] = None
    last_menu_message_id: Optional[int] = None
    last_deadline_briefing: Optional[str] = None
