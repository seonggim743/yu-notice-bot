"""
Unit tests for NotificationService formatters module.
"""

from services.notification import formatters
from models.notice import Notice


class TestFormatters:
    """Test suite for notification formatters"""

    def test_generate_clean_diff_basic(self):
        """Test basic diff generation"""
        old = "Hello World\nThis is a test"
        new = "Hello World\nThis is updated"

        diff = formatters.generate_clean_diff(old, new)

        assert "🔴" in diff  # Removed line
        assert "🟢" in diff  # Added line
        assert "test" in diff
        assert "updated" in diff

    def test_generate_clean_diff_empty(self):
        """Test diff with empty inputs"""
        assert formatters.generate_clean_diff("", "test") == ""
        assert formatters.generate_clean_diff("test", "") == ""
        assert formatters.generate_clean_diff("", "") == ""

    def test_generate_clean_diff_truncation(self):
        """Test diff truncation for long text"""
        old = "\n".join([f"Line {i}" for i in range(100)])
        new = "\n".join([f"Modified {i}" for i in range(100)])

        diff = formatters.generate_clean_diff(old, new)

        assert len(diff) > 1550
        assert "(생략)" not in diff

    def test_generate_clean_diff_telegram_context_snippet(self):
        """Test context snippets for long similar changed lines"""
        old = "오늘 오후에는 야외에 제초제 살포 작업을 진행합니다. 안전에 유의해주세요."
        new = "오늘 오후에는 야외에 수목에 방제 작업을 진행합니다. 안전에 유의해주세요."

        diff = formatters.generate_clean_diff(old, new, inline_style="telegram")

        assert "🔴" not in diff and "🟢" not in diff
        assert "<u>" not in diff and "</u>" not in diff
        assert "[" in diff and " → " in diff and "]" in diff
        assert "제초제" in diff
        assert "수목" in diff

    def test_generate_clean_diff_discord_context_snippet(self):
        """Test context snippets for long similar changed lines"""
        old = "오늘 오후에는 야외에 제초제 살포 작업을 진행합니다. 안전에 유의해주세요."
        new = "오늘 오후에는 야외에 수목에 방제 작업을 진행합니다. 안전에 유의해주세요."

        diff = formatters.generate_clean_diff(old, new, inline_style="discord")

        assert "🔴" not in diff and "🟢" not in diff
        assert "**" not in diff
        assert "[" in diff and " → " in diff and "]" in diff
        assert "제초제" in diff
        assert "수목" in diff

    def test_generate_clean_diff_context_multiple_changes(self):
        """Multiple long-line changes should be shown one snippet per change."""
        old = "접수 현황 : 25 / 30 온라인 접수 중이며 신청 마감일은 2026.05.10. 입니다."
        new = "접수 현황 : 26 / 30 온라인 접수 중이며 신청 마감일은 2026.05.11. 입니다."

        diff = formatters.generate_clean_diff(old, new, inline_style="discord")

        lines = diff.splitlines()
        assert len(lines) == 2
        assert any("[25 → 26]" in line for line in lines)
        assert any("[2026.05.10. → 2026.05.11.]" in line for line in lines)

    def test_generate_clean_diff_short_lines_stay_line_level(self):
        """Short replacements should stay as plain line-level diff"""
        diff = formatters.generate_clean_diff(
            "마감 4/6", "마감 4/8", inline_style="telegram"
        )

        assert "<u>" not in diff
        assert "🔴 마감 4/6" in diff
        assert "🟢 마감 4/8" in diff

    def test_get_category_emoji(self):
        """Test category emoji mapping"""
        assert formatters.get_category_emoji("장학") == "💰"
        assert formatters.get_category_emoji("학사") == "🎓"
        assert formatters.get_category_emoji("긴급") == "🚨"
        assert formatters.get_category_emoji("알수없음") == "📢"  # Default

    def test_get_file_emoji(self):
        """Test file extension emoji mapping"""
        assert formatters.get_file_emoji("document.pdf") == "📕"
        assert formatters.get_file_emoji("presentation.pptx") == "📙"
        assert formatters.get_file_emoji("spreadsheet.xlsx") == "📗"
        assert formatters.get_file_emoji("image.jpg") == "🖼️"
        assert formatters.get_file_emoji("unknown.xyz") == "📄"  # Default

    def test_get_site_name(self):
        """Test site name localization"""
        assert formatters.get_site_name("yu_news") == "영대소식"
        assert formatters.get_site_name("cse_notice") == "컴공공지"
        assert formatters.get_site_name("unknown") == "unknown"  # Fallback

    def test_format_summary_lines(self):
        """Test summary line formatting"""
        summary = "Line one\nLine two\nLine three"
        formatted = formatters.format_summary_lines(summary)

        lines = formatted.split("\n")
        assert all(line.startswith("- ") for line in lines)
        assert len(lines) == 3

    def test_format_summary_lines_with_hyphens(self):
        """Test summary with existing hyphens"""
        summary = "- Already formatted\nNot formatted"
        formatted = formatters.format_summary_lines(summary)

        assert formatted == "- Already formatted\n- Not formatted"

    def test_format_summary_lines_empty_lines(self):
        """Test summary with empty lines"""
        summary = "Line one\n\n\nLine two"
        formatted = formatters.format_summary_lines(summary)

        assert formatted == "- Line one\n- Line two"

    def test_escape_html(self):
        """Test HTML escaping"""
        text = "<script>alert('xss')</script>"
        escaped = formatters.escape_html(text)

        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_truncate_text(self):
        """Test text truncation"""
        text = "This is a very long text that should be truncated"
        truncated = formatters.truncate_text(text, 20)

        assert len(truncated) == 20
        assert truncated.endswith("...")
        assert "This is" in truncated

    def test_truncate_text_no_truncation(self):
        """Test truncation with short text"""
        text = "Short"
        truncated = formatters.truncate_text(text, 20)

        assert truncated == "Short"
        assert "..." not in truncated

    def test_strip_html_text_removes_images_and_truncates(self):
        raw = "<p>본문 <b>텍스트</b></p><img src='x.jpg'><script>x()</script>"

        text = formatters.strip_html_text(raw, max_length=20)

        assert "본문" in text
        assert "텍스트" in text
        assert "img" not in text
        assert "x()" not in text

    def test_create_telegram_message_quotes_summary(self):
        notice = Notice(
            site_key="yu_news",
            article_id="1",
            title="공지",
            content="<p>원문 본문</p>",
            summary="AI 요약 내용",
            url="https://example.com",
        )

        msg = formatters.create_telegram_message(notice, is_new=True)

        assert "<blockquote>AI 요약 내용</blockquote>" in msg

    def test_create_discord_embed_quotes_content_without_summary(self):
        notice = Notice(
            site_key="yu_news",
            article_id="1",
            title="공지",
            content="<p>원문 <img src='x.jpg'>본문</p>",
            url="https://example.com",
        )

        embed = formatters.create_discord_embed(notice, is_new=True)

        assert "원문" in embed["description"]
        assert "본문" in embed["description"]
        assert "<img" not in embed["description"]
