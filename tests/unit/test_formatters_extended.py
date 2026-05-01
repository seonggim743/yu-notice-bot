
import pytest
from services.notification.formatters import format_change_summary

class TestFormattersExtended:
    def test_format_change_summary_attachment_text(self):
        changes = {
            "attachment_text": "첨부파일 내용 변경됨"
        }
        result = format_change_summary(changes)
        assert "📎 **첨부파일 내용 변경**" in result
        assert "(상세 내용 확인 필요)" in result

    def test_format_change_summary_full_stack(self):
        changes = {
            "title": "'Old' -> 'New'",
            "content": "Content Changed",
            "attachment_text": "Changed",
            "image": "Changed"
        }
        result = format_change_summary(changes)
        
        assert "📝 **제목 변경**: 'Old' -> 'New'" in result
        assert "📝 **내용 변경**: Content Changed" in result
        assert "📎 **첨부파일 내용 변경**" in result
        assert "🖼️ **이미지 변경됨**" in result

    def test_format_change_summary_attachments_granular(self):
        changes = {
            "attachments_added": ["file1.hwp"],
            "attachments_removed": ["file2.pdf"],
            "attachment_text": "Changed"
        }
        result = format_change_summary(changes)
        
        assert "➕ **첨부 추가**: file1.hwp" in result
        assert "➖ **첨부 삭제**: file2.pdf" in result
        assert "📎 **첨부파일 내용 변경**" in result

    def test_format_change_summary_html_bold_for_telegram(self):
        changes = {
            "content": "변경 요약",
            "attachments_added": ["자료<1>.pdf"],
        }

        result = format_change_summary(changes, style="html")

        assert "📝 <b>내용 변경</b>: 변경 요약" in result
        assert "➕ <b>첨부 추가</b>: 자료&lt;1&gt;.pdf" in result
        assert "**내용 변경**" not in result
