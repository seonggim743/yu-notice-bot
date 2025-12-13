"""
File service - delegates to specialized file handlers.
This file maintains backward compatibility with existing code that imports FileService.
"""
import aiohttp
import io
import logging
import os
import subprocess
import tempfile
import zipfile
from typing import Optional, List

import fitz  # PyMuPDF
from PIL import Image

from services.polaris_service import PolarisService
from services.file.base import BaseFileHandler
from services.file.pdf import PDFHandler
from services.file.hwp import HWPHandler
from services.file.office import OfficeHandler
from services.file.image import ImageHandler

logger = logging.getLogger(__name__)


class FileService(BaseFileHandler):
    """
    Unified file service that delegates to specialized handlers.
    Maintains the same interface as the original FileService for backward compatibility.
    """

    def __init__(self):
        self.polaris_service = PolarisService()
        self.pdf_handler = PDFHandler()
        self.hwp_handler = HWPHandler()
        self.office_handler = OfficeHandler()
        self.image_handler = ImageHandler()

    def extract_text(self, file_data: bytes, filename: str) -> str:
        """Extracts text from PDF or HWP files."""
        ext = self.get_extension(filename)

        try:
            if ext == "pdf":
                text = self.pdf_handler.extract_text(file_data)
            elif ext == "hwp":
                text = self.hwp_handler.extract_hwp_text(file_data)
            elif ext == "hwpx":
                text = self.hwp_handler.extract_hwpx_text(file_data)
            elif ext == "docx":
                text = self.office_handler.extract_docx_text(file_data)
            elif ext == "xlsx":
                text = self.office_handler.extract_xlsx_text(file_data)
            else:
                text = ""

            # Sanitize: Remove null bytes
            if text:
                text = text.replace("\x00", "")

            return text
        except Exception as e:
            logger.error(f"[FILE] Extraction failed for {filename}: {e}")
            return ""

    def convert_to_pdf(self, file_data: bytes, filename: str) -> Optional[bytes]:
        """
        Converts Office documents (HWP, DOCX, XLSX, PPTX) to PDF.
        """
        ext = self.get_extension(filename)
        if ext == "pdf":
            return file_data

        soffice_cmd = self.get_soffice_command()

        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["HOME"] = temp_dir

            if ext == "hwp":
                return self._convert_hwp_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
            elif ext == "hwpx":
                return self._convert_hwpx_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
            else:
                return self._convert_office_to_pdf(file_data, filename, ext, temp_dir, env, soffice_cmd)

    def _convert_hwp_to_pdf(self, file_data: bytes, filename: str, temp_dir: str, env: dict, soffice_cmd: str) -> Optional[bytes]:
        """HWP conversion with multi-layered fallback strategy."""
        logger.info(f"[FILE] Starting HWP conversion for {filename}")

        input_path = os.path.join(temp_dir, "input.hwp")
        with open(input_path, "wb") as f:
            f.write(file_data)

        # Priority 1: Polaris Office
        try:
            logger.info(f"[FILE] Attempting Polaris Office conversion for {filename}")
            jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
            if jpg_files:
                logger.info(f"[FILE] Polaris conversion successful: {len(jpg_files)} images")
                return self._images_to_pdf_pil(jpg_files)
        except Exception as e:
            logger.error(f"[FILE] Polaris conversion error: {e}")

        # Priority 2: hwp5html
        logger.info(f"[FILE] Attempting hwp5html conversion for {filename}")
        png_files = self.hwp_handler.convert_hwp_to_png_via_html(file_data, filename, temp_dir)
        if png_files:
            logger.info(f"[FILE] hwp5html conversion successful: {len(png_files)} images")
            return self._images_to_pdf_pil(png_files)

        # Priority 3: Text Extraction Fallback
        if soffice_cmd:
            return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
        
        return None

    def _convert_hwpx_to_pdf(self, file_data: bytes, filename: str, temp_dir: str, env: dict, soffice_cmd: str) -> Optional[bytes]:
        """HWPX conversion."""
        logger.info(f"[FILE] Starting HWPX conversion for {filename}")

        input_path = os.path.join(temp_dir, "input.hwpx")
        with open(input_path, "wb") as f:
            f.write(file_data)

        if not zipfile.is_zipfile(input_path):
            logger.error(f"[FILE] Invalid HWPX file (not a zip): {filename}")
            return None

        # Priority 1: Polaris Office
        try:
            jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
            if jpg_files:
                return self._images_to_pdf_pil(jpg_files)
        except Exception as e:
            logger.error(f"[FILE] Polaris conversion error: {e}")

        # Priority 2: Text Extraction Fallback
        if soffice_cmd:
            return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
        
        return None

    def _convert_office_to_pdf(self, file_data: bytes, filename: str, ext: str, temp_dir: str, env: dict, soffice_cmd: str) -> Optional[bytes]:
        """Direct LibreOffice conversion for DOCX, XLSX, etc."""
        if not soffice_cmd:
            logger.warning(f"[FILE] LibreOffice not found. Skipping PDF conversion for {filename}.")
            return None

        safe_filename = f"input.{ext}"
        input_path = os.path.join(temp_dir, safe_filename)

        with open(input_path, "wb") as f:
            f.write(file_data)

        cmd = [
            soffice_cmd, "--headless", "--nologo", "--nofirststartwizard",
            "--invisible", "--convert-to", "pdf", "--outdir", temp_dir,
            f"-env:UserInstallation=file://{temp_dir}/LibreOffice_User",
            input_path,
        ]

        try:
            subprocess.run(cmd, check=True, timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            pdf_path = os.path.join(temp_dir, "input.pdf")

            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    return f.read()
            else:
                if ext in ["docx", "xlsx"]:
                    return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
                return None
        except Exception as e:
            logger.error(f"[FILE] PDF conversion error for {filename}: {e}")
            return None

    def _fallback_text_to_pdf(self, file_data: bytes, filename: str, temp_dir: str, env: dict, soffice_cmd: str) -> Optional[bytes]:
        """Fallback: Extract text and convert to PDF."""
        logger.info(f"[FILE] Attempting fallback: Convert extracted text to PDF for {filename}")
        try:
            text = self.extract_text(file_data, filename)
            if text and len(text.strip()) > 0:
                txt_path = os.path.join(temp_dir, "fallback.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)

                cmd = [
                    soffice_cmd, "--headless", "--nologo", "--nofirststartwizard",
                    "--convert-to", "pdf", "--outdir", temp_dir,
                    f"-env:UserInstallation=file://{temp_dir}/LibreOffice_User",
                    txt_path,
                ]

                subprocess.run(cmd, check=True, timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                fallback_pdf = os.path.join(temp_dir, "fallback.pdf")
                
                if os.path.exists(fallback_pdf):
                    with open(fallback_pdf, "rb") as f:
                        return f.read()
        except Exception as e:
            logger.error(f"[FILE] Fallback failed: {e}")
        
        return None

    def _images_to_pdf_pil(self, image_paths: List[str]) -> Optional[bytes]:
        """Converts a list of images to a single PDF using PIL."""
        try:
            if not image_paths:
                return None

            images = []
            for path in image_paths:
                try:
                    img = Image.open(path)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    images.append(img)
                except Exception as e:
                    logger.warning(f"[FILE] Failed to open image {path}: {e}")

            if not images:
                return None

            output = io.BytesIO()
            images[0].save(output, format='PDF', save_all=True, append_images=images[1:])
            return output.getvalue()
        except Exception as e:
            logger.error(f"[FILE] Image to PDF conversion failed: {e}")
            return None

    def generate_preview_images(
        self, file_data: bytes, filename: str, max_pages: int = 100
    ) -> List[bytes]:
        """
        Generates preview images (up to max_pages) for PDF and Office documents.
        """
        ext = self.get_extension(filename)
        supported_exts = ["pdf", "hwp", "hwpx", "doc", "docx", "xls", "xlsx", "ppt", "pptx"]

        if ext not in supported_exts:
            return []

        try:
            if ext == "hwp":
                return self._generate_hwp_preview(file_data, filename, max_pages)
            elif ext in ["xlsx", "xls"]:
                return self._generate_xlsx_preview(file_data, filename, max_pages)
            else:
                return self._generate_via_pdf(file_data, filename, max_pages)

        except Exception as e:
            logger.warning(f"[FILE] Preview generation failed for {filename}: {e}")
            return []

    def _generate_hwp_preview(self, file_data: bytes, filename: str, max_pages: int) -> List[bytes]:
        """Generate preview for HWP files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Priority 1: Polaris Office
            try:
                input_path = os.path.join(temp_dir, "input.hwp")
                with open(input_path, "wb") as f:
                    f.write(file_data)

                jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
                if jpg_files:
                    return self._process_png_files(jpg_files, max_pages)
            except Exception as e:
                logger.error(f"[FILE] Polaris conversion error: {e}")

            # Priority 2: hwp5html
            png_files = self.hwp_handler.convert_hwp_to_png_via_html(file_data, filename, temp_dir)
            if png_files:
                return self._process_png_files(png_files, max_pages)

            # Fallback to PDF
            return self._generate_via_pdf(file_data, filename, max_pages)

    def _generate_xlsx_preview(self, file_data: bytes, filename: str, max_pages: int) -> List[bytes]:
        """Generate preview for XLSX files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            png_files = self.office_handler.convert_xlsx_to_png_via_html(file_data, filename, temp_dir)
            if png_files:
                return self._process_png_files(png_files, max_pages)

            # Fallback to PDF
            return self._generate_via_pdf(file_data, filename, max_pages)

    def _generate_via_pdf(self, file_data: bytes, filename: str, max_pages: int = 5) -> List[bytes]:
        """Generate preview images via PDF conversion."""
        ext = self.get_extension(filename)

        try:
            pdf_data = file_data
            if ext != "pdf":
                pdf_data = self.convert_to_pdf(file_data, filename)
                if not pdf_data:
                    return []

            doc = fitz.open(stream=pdf_data, filetype="pdf")
            preview_images = []
            num_pages = min(doc.page_count, max_pages)

            for i in range(num_pages):
                page = doc.load_page(i)
                zoom = 2.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)

                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                max_width = 1024
                if img.width > max_width:
                    ratio = max_width / float(img.width)
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                img_buffer = io.BytesIO()
                img.save(img_buffer, format="JPEG", quality=85)
                img_buffer.seek(0)

                watermarked = self.add_watermark(img_buffer.getvalue())
                preview_images.append(watermarked)

            doc.close()
            return preview_images

        except Exception as e:
            logger.warning(f"[FILE] Preview generation failed for {filename}: {e}")
            return []

    def _process_png_files(self, png_files: List[str], max_pages: int) -> List[bytes]:
        """Process PNG files: resize, convert to JPEG, add watermark."""
        return self.image_handler.process_png_files(png_files, max_pages, self.add_watermark)

    def add_watermark(self, image_bytes: bytes, text: str = "PREVIEW") -> bytes:
        """Adds a semi-transparent watermark to the center of the image."""
        return self.image_handler.add_watermark(image_bytes, text)
