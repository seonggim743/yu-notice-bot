import pytest
import os
from parsers.html_parser import HTMLParser

@pytest.fixture
def yu_news_html():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "yu_news_list.html")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()

def test_parse_yu_news_list(yu_news_html):
    # Selectors from scraper_service.py for yu_news
    parser = HTMLParser("table tbody tr", "a", "a", ".b-view-content")
    
    items = parser.parse_list(yu_news_html, "yu_news", "https://hcms.yu.ac.kr/main/intro/yu-news.do")
    
    assert len(items) == 2
    
    # Check first item (ID 99999)
    item1 = next((i for i in items if i.article_id == "99999"), None)
    assert item1 is not None
    assert item1.title == "[Important] Scholarship Announcement"
    assert "articleNo=99999" in item1.url
    
    # Check second item (ID 88888)
    item2 = next((i for i in items if i.article_id == "88888"), None)
    assert item2 is not None
    assert item2.title == "Regular Notice Title"
    assert "articleNo=88888" in item2.url
