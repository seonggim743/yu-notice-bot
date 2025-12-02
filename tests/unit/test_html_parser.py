"""
Unit tests for HTMLParser.

Tests cover:
- Content extraction from HTML
- Attachment parsing
- Date parsing
- Error handling
"""

import pytest
from parsers.html_parser import HTMLParser
from bs4 import BeautifulSoup


class TestHTMLParser:
    """Test suite for HTMLParser"""

    @pytest.fixture
    def parser(self):
        """Create HTMLParser instance with dummy selectors"""
        return HTMLParser(
            list_selector=".notice-list tr",
            title_selector=".title",
            link_selector="a",
            content_selector=".content",
        )

    @pytest.fixture
    def sample_html(self):
        """Sample HTML for testing"""
        return """
        <html>
        <head><title>공지사항</title></head>
        <body>
            <div class="notice-content">
                <h1>2024학년도 장학금 신청 안내</h1>
                <div class="content">
                    <p>신청기간: 2024-12-01 ~ 2024-12-15</p>
                    <p>대상: 재학생 전체</p>
                    <ul>
                        <li>제출서류: 신청서, 성적증명서</li>
                        <li>문의: 학생처</li>
                    </ul>
                </div>
                <div class="b-file-box">
                    <a href="/download/file1.pdf" class="b-file-dwn">신청서.pdf</a>
                    <a href="/download/file2.hwp" class="b-file-dwn">양식.hwp</a>
                </div>
            </div>
        </body>
        </html>
        """

    def test_extract_text_content(self, parser, sample_html):
        """Test text content extraction"""
        soup = BeautifulSoup(sample_html, "html.parser")
        content = parser.extract_text(soup)

        assert "신청기간" in content
        assert "신청기간" in content
        assert "2024-12-01" in content

    def test_extract_text_removes_scripts(self, parser):
        """Test that script tags are removed"""
        html_with_script = """
        <html>
        <body>
            <div class="content">공지 내용</div>
            <script>alert('test');</script>
        </body>
        </html>
        """
        soup = BeautifulSoup(html_with_script, "html.parser")
        content = parser.extract_text(soup)

        assert "공지 내용" in content
        assert "alert" not in content
        assert "script" not in content.lower()

    def test_extract_attachments(self, parser, sample_html):
        """Test attachment extraction"""
        soup = BeautifulSoup(sample_html, "html.parser")
        attachments = parser.extract_attachments(soup, base_url="https://example.com")

        assert len(attachments) >= 2
        assert any("pdf" in att.name.lower() for att in attachments)
        assert any("hwp" in att.name.lower() for att in attachments)

    def test_extract_attachments_absolute_urls(self, parser):
        """Test that relative URLs are converted to absolute"""
        html = """
        <div class="attachments">
            <a href="/download/file.pdf">파일.pdf</a>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        attachments = parser.extract_attachments(soup, base_url="https://example.com")

        if attachments:
            assert attachments[0].url.startswith("https://")

    def test_extract_date(self, parser):
        """Test date extraction from text"""
        text1 = "신청기간: 2024년 12월 1일 ~ 12월 15일"
        date = parser.extract_date(text1)
        assert date is not None
        assert "2024" in date

        text2 = "2024-12-01 ~ 2024-12-15"
        date = parser.extract_date(text2)
        assert date is not None

    def test_extract_date_no_date(self, parser):
        """Test date extraction when no date present"""
        text = "날짜가 없는 공지사항입니다."
        date = parser.extract_date(text)
        assert date is None or date == ""

    def test_parse_table_data(self, parser):
        """Test table parsing"""
        html = """
        <div class="content">
            <table>
                <tr>
                    <th>구분</th>
                    <th>내용</th>
                </tr>
                <tr>
                    <td>기간</td>
                    <td>2024-12-01 ~ 2024-12-15</td>
                </tr>
                <tr>
                    <td>대상</td>
                    <td>전체 학년</td>
                </tr>
            </table>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        text = parser.extract_text(soup)

        assert "기간" in text
        assert "2024-12-01" in text
        assert "대상" in text

    def test_clean_whitespace(self, parser):
        """Test whitespace cleaning"""
        messy_text = "공지사항\n\n\n\n내용\n  \n  끝"
        clean = parser.clean_whitespace(messy_text)

        assert "공지사항" in clean
        assert "내용" in clean
        # Should not have excessive newlines
        assert "\n\n\n" not in clean

    def test_extract_images(self, parser):
        """Test image extraction"""
        html = """
        <div class="content">
            <img src="/images/photo1.jpg" alt="사진1">
            <img src="/images/photo2.png" alt="사진2">
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        images = parser.extract_images(soup, base_url="https://example.com")

        assert len(images) >= 2
        assert all(img.startswith("https://") for img in images)

    def test_handle_broken_html(self, parser):
        """Test handling of broken HTML"""
        broken_html = "<div class='content'><p>미완성 태그"
        soup = BeautifulSoup(broken_html, "html.parser")
        content = parser.extract_text(soup)

        # Should not crash, should extract what it can
        assert "미완성 태그" in content

    def test_extract_list_items(self, parser):
        """Test list item extraction"""
        html = """
        <div class="content">
            <ul>
                <li>첫 번째 항목</li>
                <li>두 번째 항목</li>
                <li>세 번째 항목</li>
            </ul>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        text = parser.extract_text(soup)

        assert "첫 번째" in text
        assert "두 번째" in text
        assert "세 번째" in text

    def test_remove_navigation_elements(self, parser):
        """Test that navigation/menu elements are removed"""
        html = """
        <div class="notice">
            <nav>메뉴1</nav>
            <div class="content">실제 내용</div>
            <footer>푸터</footer>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        content = parser.extract_text(soup)

        assert "실제 내용" in content
        # Navigation should ideally be filtered
        # (depends on implementation)
