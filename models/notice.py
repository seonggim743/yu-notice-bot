from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

class Attachment(BaseModel):
    name: str
    url: str

class Notice(BaseModel):
    site_key: str
    article_id: str
    title: str
    content: str = ""
    url: str
    category: str = "일반"
    published_at: Optional[datetime] = None
    attachments: List[Attachment] = Field(default_factory=list)
    image_url: Optional[str] = None
    
    # AI Metadata
    summary: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    target_grades: List[int] = Field(default_factory=list)
    target_dept: Optional[str] = None
    
    # Internal
    content_hash: Optional[str] = None
    embedding: Optional[List[float]] = None
    change_details: Optional[Dict[str, Any]] = Field(default_factory=dict)
