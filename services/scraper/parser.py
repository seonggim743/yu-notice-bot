from typing import List
from models.notice import Notice
from parsers.html_parser import HTMLParser
from core.logger import get_logger

logger = get_logger(__name__)

class NoticeParser:
    """
    Handles parsing of notices using specific strategies.
    Wraps HTMLParser and adds sanitization/validation logic.
    """
    def parse_list(self, parser: HTMLParser, html: str, site_key: str, base_url: str) -> List[Notice]:
        """
        Parses the list page and returns a list of Notice objects.
        Returns them in reverse chronological order (oldest first).
        """
        items = parser.parse_list(html, site_key, base_url)
        # IMPORTANT: Process oldest first (reverse chronological order)
        items.reverse()
        return items

    def parse_detail(self, parser: HTMLParser, html: str, item: Notice) -> Notice:
        """
        Parses the detail page and updates the Notice object.
        Also performs content sanitization.
        """
        item = parser.parse_detail(html, item)
        
        # Sanitize content (remove null bytes)
        if item.content:
            item.content = item.content.replace("\x00", "")
            item.content = item.content.strip()  # Normalize whitespace
            
        return item
