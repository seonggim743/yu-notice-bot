"""
HashCalculator component for notice content hashing.
Extracted from ScraperService for Single Responsibility Principle.
"""
import hashlib
from typing import List

from models.notice import Notice


class HashCalculator:
    """
    Calculates content hashes for notices to detect changes.
    Uses SHA-256 for reliable change detection.
    """
    
    @staticmethod
    def calculate_hash(notice: Notice) -> str:
        """
        Calculates a unique hash for a notice based on its content.
        
        Hash includes:
        - Title
        - Content
        - Image URLs (sorted)
        - Attachments (name, URL, size, ETag - sorted)
        - Attachment text (extracted text from files)
        
        Args:
            notice: Notice object to hash
            
        Returns:
            SHA-256 hash string
        """
        # Sort attachments for consistent hashing
        sorted_atts = sorted([
            f"{a.name}|{a.url}|{a.file_size or 0}|{a.etag or ''}"
            for a in notice.attachments
        ])
        att_str = "".join(sorted_atts)
        
        # Sort images for consistent hashing
        img_str = "|".join(sorted(notice.image_urls)) if notice.image_urls else ""
        
        # Include attachment text
        att_text = notice.attachment_text or ""
        
        # Combine all fields
        raw = f"{notice.title}{notice.content}{img_str}{att_str}{att_text}"
        
        return hashlib.sha256(raw.encode()).hexdigest()
    
    @staticmethod
    def calculate_simple_hash(text: str) -> str:
        """
        Calculates a simple hash for any text content.
        
        Args:
            text: Text to hash
            
        Returns:
            SHA-256 hash string
        """
        return hashlib.sha256(text.encode()).hexdigest()
    
    @staticmethod
    def calculate_attachment_hash(name: str, url: str, size: int = 0, etag: str = "") -> str:
        """
        Calculates a hash for a single attachment.
        
        Args:
            name: Attachment filename
            url: Attachment URL
            size: File size in bytes
            etag: ETag header value
            
        Returns:
            SHA-256 hash string
        """
        raw = f"{name}|{url}|{size}|{etag}"
        return hashlib.sha256(raw.encode()).hexdigest()
