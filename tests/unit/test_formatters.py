"""
Unit tests for NotificationService formatters module.
"""

from services.notification import formatters


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
