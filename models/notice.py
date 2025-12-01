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
    tags: List[str] = Field(default_factory=list)  # AI-selected tags (1-5)
    published_at: Optional[datetime] = None
    attachments: List[Attachment] = Field(default_factory=list)
    image_url: Optional[str] = None
    
    # AI Metadata
    summary: str = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    target_grades: List[int] = Field(default_factory=list)
    target_dept: Optional[str] = None
    
    # Enhanced AI Metadata (Tier 2)
    deadline: Optional[str] = None  # YYYY-MM-DD
    eligibility: List[str] = Field(default_factory=list)
    
    # PDF Preview (Tier 1) - Memory Only
    preview_image: Optional[bytes] = Field(default=None, exclude=True) # Exclude from DB dump
    
    # Internal
    content_hash: Optional[str] = None
    embedding: Optional[List[float]] = None
    change_details: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    # Discord Thread Tracking
    discord_thread_id: Optional[str] = None
