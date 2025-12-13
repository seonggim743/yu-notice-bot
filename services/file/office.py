"""
Office document handling (DOCX, XLSX, PPTX).
"""
import io
import logging
import os
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional, List

logger = logging.getLogger(__name__)


class OfficeHandler:
    """Handles Office document text extraction and conversion."""

    def extract_docx_text(self, data: bytes) -> str:
        """Extracts text from DOCX (Zip + XML)."""
        try:
            f = io.BytesIO(data)
            if not zipfile.is_zipfile(f):
                return ""

            text = ""
            with zipfile.ZipFile(f) as zf:
                if "word/document.xml" in zf.namelist():
                    with zf.open("word/document.xml") as xml_file:
                        tree = ET.parse(xml_file)
                        root = tree.getroot()
                        for elem in root.iter():
                            if elem.tag.endswith("}t"):  # w:t
                                if elem.text:
                                    text += elem.text
                            elif elem.tag.endswith("}p"):  # w:p
                                text += "\n"
            return text
        except Exception as e:
            logger.error(f"[FILE] DOCX parse error: {e}")
            return ""

    def extract_xlsx_text(self, data: bytes) -> str:
        """Extracts text from XLSX (Zip + XML)."""
        try:
            f = io.BytesIO(data)
            if not zipfile.is_zipfile(f):
                return ""

            text = []
            with zipfile.ZipFile(f) as zf:
                if "xl/sharedStrings.xml" in zf.namelist():
                    with zf.open("xl/sharedStrings.xml") as xml_file:
                        tree = ET.parse(xml_file)
                        root = tree.getroot()
                        for elem in root.iter():
                            if elem.tag.endswith("}t"):
                                if elem.text:
                                    text.append(elem.text)
            return "\n".join(text)
        except Exception as e:
            logger.error(f"[FILE] XLSX parse error: {e}")
            return ""

    def convert_xlsx_to_png_via_html(
        self, file_data: bytes, filename: str, temp_dir: str
    ) -> List[str]:
        """
        Convert XLSX to PNG using xlsx2html + Playwright.
        Preserves formatting better than LibreOffice.
        Returns list of PNG file paths.
        """
        try:
            # Save XLSX file
            xlsx_path = os.path.join(temp_dir, "input.xlsx")
            with open(xlsx_path, "wb") as f:
                f.write(file_data)

            # Step 1: XLSX → HTML using xlsx2html
            logger.info("[FILE] Converting XLSX to HTML using xlsx2html...")

            try:
                from xlsx2html import xlsx2html

                html_path = os.path.join(temp_dir, "index.html")
                xlsx2html(xlsx_path, html_path)

                if not os.path.exists(html_path):
                    logger.warning("[FILE] HTML file not created by xlsx2html")
                    return []

                logger.info("[FILE] XLSX→HTML conversion successful")

            except ImportError:
                logger.error("[FILE] xlsx2html not installed")
                return []
            except Exception as e:
                logger.error(f"[FILE] xlsx2html conversion error: {e}")
                return []

            # Step 2: HTML → Full-page Screenshot using Playwright
            return self._render_xlsx_html_to_png(html_path, temp_dir)

        except Exception as e:
            logger.error(f"[FILE] XLSX→PNG conversion error: {e}")
            return []

    def _render_xlsx_html_to_png(self, html_path: str, temp_dir: str) -> List[str]:
        """Render XLSX HTML to PNG images using Playwright."""
        try:
            logger.info("[FILE] Rendering HTML to full-page screenshot using Playwright...")

            output_png = os.path.join(temp_dir, "fullpage.png")

            # Create a simple Python script to run Playwright
            script_content = f'''
from playwright.sync_api import sync_playwright
import sys

html_path = r"{os.path.abspath(html_path).replace(os.sep, '/')}"
output_path = r"{output_png}"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file:///{{html_path}}")
        page.wait_for_timeout(1000)
        
        # Dynamic Viewport Sizing
        body_handle = page.query_selector("body")
        if body_handle:
            box = body_handle.bounding_box()
            if box:
                width = int(box['width']) + 50
                height = int(box['height']) + 50
                page.set_viewport_size({{"width": width, "height": height}})
        
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    sys.exit(0)
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    sys.exit(1)
'''

            script_path = os.path.join(temp_dir, "render_xlsx_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_content)

            result = subprocess.run(
                ["python", script_path],
                timeout=30,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir,
            )

            if os.path.exists(output_png) and os.path.getsize(output_png) > 0:
                logger.info("[FILE] Full-page screenshot successful")
                return self._split_xlsx_image(output_png, temp_dir)
            else:
                logger.warning("[FILE] Screenshot file not created or empty")
                return []

        except subprocess.TimeoutExpired:
            logger.error("[FILE] Playwright rendering timed out")
            return []
        except Exception as e:
            logger.error(f"[FILE] Playwright rendering error: {e}")
            return []

    def _split_xlsx_image(self, output_png: str, temp_dir: str) -> List[str]:
        """Split a long XLSX screenshot into page-sized chunks."""
        try:
            from PIL import Image

            img = Image.open(output_png)
            img_width, img_height = img.size

            logger.info(f"[FILE] Full image size: {img_width}x{img_height}")

            # Calculate number of pages based on A4 ratio
            a4_ratio = 1.414
            expected_page_height = max(int(img_width * a4_ratio), 1000)
            num_pages = max(1, round(img_height / expected_page_height))
            page_height = img_height // num_pages

            logger.info(f"[FILE] Splitting into {num_pages} pages")

            png_files = []
            for page_num in range(num_pages):
                y_start = page_num * page_height
                y_end = img_height if page_num == num_pages - 1 else (page_num + 1) * page_height

                page_img = img.crop((0, y_start, img_width, y_end))
                png_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")
                page_img.save(png_path)
                png_files.append(png_path)

            img.close()
            return png_files

        except Exception as e:
            logger.error(f"[FILE] Image splitting error: {e}")
            return []
