import io
import zipfile
from unittest.mock import Mock

from services.file_service import FileService


def _minimal_hwpx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Contents/section0.xml", "<root><p>sample</p></root>")
    return buffer.getvalue()


def test_hwpx_conversion_uses_libreoffice_before_text_fallback(tmp_path):
    """HWPX conversion should keep a preview path even when Polaris fails."""
    file_service = FileService()
    file_service.polaris_service.convert_to_jpg = Mock(return_value=[])
    file_service._convert_office_to_pdf = Mock(return_value=b"layout-pdf")
    file_service._fallback_text_to_pdf = Mock(return_value=b"text-pdf")

    result = file_service._convert_hwpx_to_pdf(
        _minimal_hwpx_bytes(),
        "sample.hwpx",
        str(tmp_path),
        {},
        "soffice",
    )

    assert result == b"layout-pdf"
    file_service._convert_office_to_pdf.assert_called_once()
    file_service._fallback_text_to_pdf.assert_not_called()


def test_hwpx_conversion_falls_back_to_text_pdf_when_libreoffice_fails(tmp_path):
    """Text fallback is still useful when layout-preserving HWPX conversion fails."""
    file_service = FileService()
    file_service.polaris_service.convert_to_jpg = Mock(return_value=[])
    file_service._convert_office_to_pdf = Mock(return_value=None)
    file_service._fallback_text_to_pdf = Mock(return_value=b"text-pdf")

    result = file_service._convert_hwpx_to_pdf(
        _minimal_hwpx_bytes(),
        "sample.hwpx",
        str(tmp_path),
        {},
        "soffice",
    )

    assert result == b"text-pdf"
    file_service._convert_office_to_pdf.assert_called_once()
    file_service._fallback_text_to_pdf.assert_called_once()


def test_hwpx_preview_uses_pdf_fallback_path():
    """HWPX previews should be generated through the same PDF fallback path."""
    file_service = FileService()
    file_service._generate_via_pdf = Mock(return_value=[b"fallback-preview"])

    result = file_service.generate_preview_images(
        _minimal_hwpx_bytes(),
        "sample.hwpx",
        max_pages=3,
    )

    assert result == [b"fallback-preview"]
    file_service._generate_via_pdf.assert_called_once_with(
        _minimal_hwpx_bytes(),
        "sample.hwpx",
        3,
    )
