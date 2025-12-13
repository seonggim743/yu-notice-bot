"""
PDF file handling.
"""
import io
import logging
import tempfile
import os
from typing import Optional, List

import fitz  # PyMuPDF
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PDFHandler:
    """Handles PDF text extraction and preview generation."""

    def extract_text(self, data: bytes) -> str:
        """Extracts text from PDF using pypdf."""
        try:
            reader = PdfReader(io.BytesIO(data))
            text = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text.append(extracted)
            return "\n".join(text)
        except Exception as e:
            logger.error(f"[FILE] PDF parse error: {e}")
            return ""

    def generate_preview_images(
        self, file_data: bytes, max_pages: int = 5
    ) -> List[bytes]:
        """
        Generate preview images from PDF using PyMuPDF.
        Returns list of JPEG bytes.
        """
        images = []
        try:
            doc = fitz.open(stream=file_data, filetype="pdf")
            page_count = min(len(doc), max_pages)

            for page_num in range(page_count):
                page = doc[page_num]

                # Render at 2x resolution for better quality
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat)

                # Convert to JPEG bytes
                img_bytes = pix.tobytes("jpeg")
                images.append(img_bytes)

            doc.close()
        except Exception as e:
            logger.error(f"[FILE] PDF preview generation error: {e}")

        return images
