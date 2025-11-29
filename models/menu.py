from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date
import uuid

class Menu(BaseModel):
    id: Optional[uuid.UUID] = None
    notice_id: uuid.UUID
    image_url: str
    raw_text: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.now)
