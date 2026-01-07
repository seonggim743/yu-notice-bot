"""
HWP/HWPX file handling.
"""
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET
from typing import Optional, List

import olefile

logger = logging.getLogger(__name__)


class HWPHandler:
    """Handles HWP and HWPX text extraction and conversion."""

    def extract_hwp_text(self, data: bytes) -> str:
        """
        Extracts text from HWP 5.0 files using olefile.
        Logic: Open OLE -> Find BodyText/Section* -> Decompress (zlib) -> Decode (UTF-16LE)
        """
        try:
            f = io.BytesIO(data)
            ole = olefile.OleFileIO(f)

            dirs = ole.listdir()
            body_sections = [d for d in dirs if d[0] == "BodyText"]

            text = ""
            for section in body_sections:
                stream = ole.openstream(section)
                section_data = stream.read()

                # HWP 5.0 BodyText is zlib compressed
                # Usually raw deflate, so wbits=-15
                try:
                    decompressed = zlib.decompress(section_data, -15)
                    # Text is UTF-16LE
                    section_text = decompressed.decode("utf-16-le", errors="ignore")

                    # Clean up: Filter for printable characters
                    cleaned_text = ""
                    for char in section_text:
                        code = ord(char)
                        if (
                            (0xAC00 <= code <= 0xD7A3)  # Hangul Syllables
                            or (0x0020 <= code <= 0x007E)  # Basic Latin
                            or (code == 0x000A)  # Newline
                            or (code == 0x0009)  # Tab
                            or (0x3130 <= code <= 0x318F)  # Hangul Compatibility Jamo
                            or (0x1100 <= code <= 0x11FF)  # Hangul Jamo
                        ):
                            cleaned_text += char
                        else:
                            if code > 0x001F:
                                cleaned_text += " "

                    # Collapse multiple spaces
                    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

                    if len(cleaned_text) > 5:  # Ignore very short garbage sections
                        text += cleaned_text + "\n"
                except Exception as zlib_error:
                    logger.warning(f"[FILE] HWP zlib error: {zlib_error}")
                    continue

            return text
        except Exception as e:
            logger.error(f"[FILE] HWP parse error: {e}")
            return ""

    def extract_hwpx_text(self, data: bytes) -> str:
        """
        Extracts text from HWPX (Zip + XML).
        """
        try:
            f = io.BytesIO(data)
            if not zipfile.is_zipfile(f):
                return ""

            text = ""
            with zipfile.ZipFile(f) as zf:
                # Find section XMLs in Contents/
                for name in zf.namelist():
                    if name.startswith("Contents/section") and name.endswith(".xml"):
                        with zf.open(name) as xml_file:
                            tree = ET.parse(xml_file)
                            root = tree.getroot()
                            # Extract all text from XML
                            for elem in root.iter():
                                if elem.text:
                                    text += elem.text + " "
            return text
        except Exception as e:
            logger.error(f"[FILE] HWPX parse error: {e}")
            return ""

    def convert_hwp_to_odt(self, file_data: bytes, temp_dir: str) -> Optional[str]:
        """
        Converts HWP file to ODT format using pyhwp.
        Returns path to ODT file if successful, None otherwise.
        """
        try:
            # Save HWP file
            hwp_path = os.path.join(temp_dir, "input.hwp")
            with open(hwp_path, "wb") as f:
                f.write(file_data)

            # Output ODT path
            odt_path = os.path.join(temp_dir, "input.odt")

            logger.info("[FILE] Converting HWP to ODT using pyhwp...")

            # Use hwp5odt command
            if not shutil.which("hwp5odt"):
                logger.warning("[FILE] hwp5odt not found in PATH")
                return None

            cmd = ["hwp5odt", "--output", odt_path, hwp_path]

            result = subprocess.run(
                cmd,
                timeout=60,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Check if ODT file was created regardless of exit code
            if os.path.exists(odt_path) and os.path.getsize(odt_path) > 0:
                logger.info(
                    f"[FILE] HWP→ODT conversion successful (exit code: {result.returncode})"
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode() if result.stderr else ""
                    if "RelaxNG" in stderr or "RELAXNG" in stderr:
                        logger.info(
                            "[FILE] RelaxNG validation warnings ignored, ODT file created successfully"
                        )
                    else:
                        logger.warning(f"[FILE] Conversion warnings: {stderr[:200]}")
                return odt_path
            else:
                logger.warning("[FILE] ODT file not created or empty")
                return None

        except subprocess.TimeoutExpired:
            logger.error("[FILE] HWP→ODT conversion timed out")
            return None
        except Exception as e:
            logger.error(f"[FILE] Unexpected error during HWP→ODT conversion: {e}")
            return None

    def convert_hwp_to_png_via_html(
        self, file_data: bytes, filename: str, temp_dir: str
    ) -> List[str]:
        """
        Convert HWP to PNG using hwp5html + Playwright.
        This preserves layout better than LibreOffice by using HTML/CSS absolute positioning.
        Returns list of PNG file paths.
        """
        try:
            # Save HWP file
            hwp_path = os.path.join(temp_dir, "input.hwp")
            with open(hwp_path, "wb") as f:
                f.write(file_data)

            # Step 1: HWP → HTML using hwp5html
            logger.info("[FILE] Converting HWP to HTML using hwp5html...")

            if not shutil.which("hwp5html"):
                logger.warning("[FILE] hwp5html not found in PATH")
                return []

            cmd = ["hwp5html", hwp_path]

            result = subprocess.run(
                cmd,
                timeout=60,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir,
            )

            # hwp5html creates 'input' directory containing the XHTML
            html_dir = os.path.join(temp_dir, "input")
            index_html = os.path.join(html_dir, "index.xhtml")

            if not os.path.exists(index_html):
                # Try alternative locations
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    if os.path.isdir(item_path):
                        for name in ["index.xhtml", "index.html"]:
                            possible_html = os.path.join(item_path, name)
                            if os.path.exists(possible_html):
                                index_html = possible_html
                                break

            if not os.path.exists(index_html):
                logger.warning(f"[FILE] HTML file not found at {index_html}")
                return []

            logger.info("[FILE] HWP→HTML conversion successful")

            # Step 2: HTML → Full-page Screenshot, then split by height
            return self._render_html_to_png(index_html, temp_dir)

        except subprocess.TimeoutExpired:
            logger.error("[FILE] HWP→HTML conversion timed out")
            return []
        except Exception as e:
            logger.error(f"[FILE] HWP→HTML conversion error: {e}")
            return []

    def _render_html_to_png(self, index_html: str, temp_dir: str) -> List[str]:
        """Render HTML to PNG images using Playwright."""
        try:
            logger.info("[FILE] Rendering HTML to full-page screenshot using Playwright...")

            output_png = os.path.join(temp_dir, "fullpage.png")

            # Create a simple Python script to run Playwright
            script_content = f'''
from playwright.sync_api import sync_playwright
import sys

html_path = r"{os.path.abspath(index_html).replace(os.sep, '/')}"
output_path = r"{output_png}"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={{"width": 820, "height": 1200}})
        page.goto(f"file:///{{html_path}}")
        page.wait_for_timeout(1000)
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    sys.exit(0)
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    sys.exit(1)
'''

            script_path = os.path.join(temp_dir, "render_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_content)

            # Run the script in a subprocess
            result = subprocess.run(
                ["python", script_path],
                timeout=30,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir,
            )

            if os.path.exists(output_png) and os.path.getsize(output_png) > 0:
                logger.info("[FILE] Full-page screenshot successful")
                return self._split_image_to_pages(output_png, temp_dir)
            else:
                logger.warning("[FILE] Screenshot file not created or empty")
                return []

        except subprocess.TimeoutExpired:
            logger.error("[FILE] Playwright rendering timed out")
            return []
        except Exception as e:
            logger.error(f"[FILE] Playwright rendering error: {e}")
            return []

    def _split_image_to_pages(self, output_png: str, temp_dir: str) -> List[str]:
        """Split a long screenshot into page-sized chunks."""
        try:
            from PIL import Image

            img = Image.open(output_png)
            img_width, img_height = img.size

            logger.info(f"[FILE] Full image size: {img_width}x{img_height}")

            # Calculate number of pages based on A4 ratio
            a4_ratio = 1.414
            expected_page_height = int(img_width * a4_ratio)
            num_pages = max(1, round(img_height / expected_page_height))
            page_height = img_height // num_pages

            logger.info(f"[FILE] Splitting into {num_pages} pages (approx {page_height}px each)")

            png_files = []
            for page_num in range(num_pages):
                y_start = page_num * page_height
                y_end = img_height if page_num == num_pages - 1 else (page_num + 1) * page_height

                page_img = img.crop((0, y_start, img_width, y_end))
                png_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")
                page_img.save(png_path)
                png_files.append(png_path)
                logger.info(f"[FILE] Generated page {page_num + 1}/{num_pages}")

            img.close()
            return png_files

        except Exception as e:
            logger.error(f"[FILE] Image splitting error: {e}")
            return []
