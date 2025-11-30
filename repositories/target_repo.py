from typing import List, Dict, TypedDict
from parsers.html_parser import HTMLParser

class TargetConfig(TypedDict):
    key: str
    url: str
    base_url: str
    parser: HTMLParser

class TargetRepository:
    """
    Repository for managing scraping targets.
    Currently hardcoded, but designed to be extensible (e.g., load from DB/Config).
    """
    
    @staticmethod
    def get_all_targets() -> List[TargetConfig]:
        return [
            {
                "key": "yu_news",
                "url": "https://hcms.yu.ac.kr/main/intro/yu-news.do",
                "base_url": "https://hcms.yu.ac.kr/main/intro/yu-news.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "cse_notice",
                "url": "https://www.yu.ac.kr/cse/community/notice.do",
                "base_url": "https://www.yu.ac.kr/cse/community/notice.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "bachelor_guide",
                "url": "https://hcms.yu.ac.kr/main/bachelor/bachelor-guide.do?mode=list&articleLimit=30",
                "base_url": "https://hcms.yu.ac.kr/main/bachelor/bachelor-guide.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "calendar",
                "url": "https://hcms.yu.ac.kr/main/bachelor/calendar.do",
                "base_url": "https://hcms.yu.ac.kr/main/bachelor/calendar.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "dormitory_notice",
                "url": "https://www.yu.ac.kr/dormi/community/notice.do",
                "base_url": "https://www.yu.ac.kr/dormi/community/notice.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            },
            {
                "key": "dormitory_menu",
                "url": "https://www.yu.ac.kr/dormi/community/menu.do",
                "base_url": "https://www.yu.ac.kr/dormi/community/menu.do",
                "parser": HTMLParser("table tbody tr", "a", "a", ".b-view-content")
            }
        ]

    @staticmethod
    def get_target_by_key(key: str) -> Dict:
        targets = TargetRepository.get_all_targets()
        for target in targets:
            if target['key'] == key:
                return target
        return None
