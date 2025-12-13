import aiohttp
import io
import zlib
import olefile
import logging
from typing import Optional, List
import fitz  # PyMuPDF
import subprocess
import os
import shutil
import tempfile
import zipfile
from pypdf import PdfReader
from services.polaris_service import PolarisService

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.polaris_service = PolarisService()

    async def download_file(
        self, session: aiohttp.ClientSession, url: str, headers: dict = None
    ) -> Optional[bytes]:
        """Downloads a file into memory."""
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    logger.warning(f"[FILE] Download failed: {resp.status} for {url}")
                    return None
        except Exception as e:
            logger.error(f"[FILE] Download error: {e}")
            return None

    def extract_text(self, file_data: bytes, filename: str) -> str:
        """Extracts text from PDF or HWP files."""
        ext = filename.split(".")[-1].lower() if "." in filename else ""

        try:
            if ext == "pdf":
                text = self._extract_pdf_text(file_data)
            elif ext == "hwp":
                text = self._extract_hwp_text(file_data)
            elif ext == "hwpx":
                text = self._extract_hwpx_text(file_data)
            elif ext == "docx":
                text = self._extract_docx_text(file_data)
            elif ext == "xlsx":
                text = self._extract_xlsx_text(file_data)
            else:
                text = ""

            # Sanitize: Remove null bytes
            if text:
                text = text.replace("\x00", "")

            return text
        except Exception as e:
            logger.error(f"[FILE] Extraction failed for {filename}: {e}")
            return ""

    def _extract_pdf_text(self, data: bytes) -> str:
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

    def _extract_hwp_text(self, data: bytes) -> str:
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
                data = stream.read()

                # HWP 5.0 BodyText is zlib compressed
                # Usually raw deflate, so wbits=-15
                try:
                    decompressed = zlib.decompress(data, -15)
                    # Text is UTF-16LE
                    # But it contains control characters and struct formatting.
                    # A simple decode might be messy but better than nothing for LLM.
                    # We filter for printable characters or just decode.
                    section_text = decompressed.decode("utf-16-le", errors="ignore")

                    # Clean up: Filter for printable characters
                    # HWP text often contains control codes mixed with text.
                    # We keep: Korean (Hangul), English, Numbers, Punctuation, Whitespace
                    cleaned_text = ""
                    for char in section_text:
                        # Check if character is printable
                        # Hangul Syllables: 0xAC00-0xD7A3
                        # Hangul Jamo: 0x1100-0x11FF
                        # Hangul Compatibility Jamo: 0x3130-0x318F
                        # Basic Latin + Latin-1 Supplement: 0x0020-0x00FF
                        # Common Punctuation
                        code = ord(char)
                        if (
                            (0xAC00 <= code <= 0xD7A3)
                            or (0x0020 <= code <= 0x007E)
                            or (code == 0x000A)
                            or (code == 0x0009)
                            or (0x3130 <= code <= 0x318F)
                            or (0x1100 <= code <= 0x11FF)
                        ):
                            cleaned_text += char
                        else:
                            # Replace unknown/control chars with space if they are not just nulls
                            if code > 0x001F:
                                cleaned_text += " "

                    # Collapse multiple spaces
                    import re

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

    def _extract_hwpx_text(self, data: bytes) -> str:
        """
        Extracts text from HWPX (Zip + XML).
        """
        import zipfile
        import xml.etree.ElementTree as ET

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
                            # HWPX uses <hp:t> for text
                            # We need to handle namespaces properly or just iterate all elements
                            for elem in root.iter():
                                if elem.text:
                                    text += elem.text + " "
            return text
        except Exception as e:
            logger.error(f"[FILE] HWPX parse error: {e}")
            return ""

    def _extract_docx_text(self, data: bytes) -> str:
        """Extracts text from DOCX (Zip + XML)."""
        import zipfile
        import xml.etree.ElementTree as ET

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

    def _extract_xlsx_text(self, data: bytes) -> str:
        """Extracts text from XLSX (Zip + XML)."""
        import zipfile
        import xml.etree.ElementTree as ET

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

    def _get_soffice_command(self) -> Optional[str]:
        """
        Tries to find the LibreOffice 'soffice' command.
        Checks PATH first, then common Windows paths.
        """
        # 1. Check PATH
        if shutil.which("soffice"):
            return "soffice"

        # 2. Check Common Windows Paths
        windows_paths = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for path in windows_paths:
            if os.path.exists(path):
                return path

        return None

    def _convert_hwp_to_odt(self, file_data: bytes, temp_dir: str) -> Optional[str]:
        """
        Converts HWP file to ODT format using pyhwp.
        Returns path to ODT file if successful, None otherwise.
        Note: Ignores RelaxNG validation errors as they don't prevent ODT creation.
        """
        try:
            # Save HWP file
            hwp_path = os.path.join(temp_dir, "input.hwp")
            with open(hwp_path, "wb") as f:
                f.write(file_data)
            
            # Output ODT path
            odt_path = os.path.join(temp_dir, "input.odt")
            
            logger.info(f"[FILE] Converting HWP to ODT using pyhwp...")
            
            # Use hwp5odt command
            if not shutil.which("hwp5odt"):
                logger.warning("[FILE] hwp5odt not found in PATH")
                return None
            
            cmd = [
                "hwp5odt",
                "--output", odt_path,
                hwp_path
            ]
            
            # Run conversion
            # Note: We don't use check=True because validation errors are common
            # but don't prevent ODT file creation. We only care if the file is created.
            result = subprocess.run(
                cmd,
                timeout=60,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            # Check if ODT file was created regardless of exit code
            if os.path.exists(odt_path) and os.path.getsize(odt_path) > 0:
                logger.info(f"[FILE] HWP→ODT conversion successful (exit code: {result.returncode})")
                # Log warnings if there were validation errors, but continue
                if result.returncode != 0:
                    stderr = result.stderr.decode() if result.stderr else ''
                    if 'RelaxNG' in stderr or 'RELAXNG' in stderr:
                        logger.info(f"[FILE] RelaxNG validation warnings ignored, ODT file created successfully")
                    else:
                        logger.warning(f"[FILE] Conversion warnings: {stderr[:200]}")
                return odt_path
            else:
                logger.warning(f"[FILE] ODT file not created or empty")
                logger.warning(f"[FILE] Exit code: {result.returncode}")
                logger.warning(f"[FILE] STDERR: {result.stderr.decode()[:500] if result.stderr else ''}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error(f"[FILE] HWP→ODT conversion timed out")
            return None
        except Exception as e:
            logger.error(f"[FILE] Unexpected error during HWP→ODT conversion: {e}")
            return None

    def _fallback_text_to_pdf(self, file_data: bytes, filename: str, temp_dir: str, env: dict, soffice_cmd: str) -> Optional[bytes]:
        """
        Fallback method: Extracts text from the file and converts it to a PDF.
        Used when direct conversion or other strategies fail.
        """
        logger.info(f"[FILE] Attempting fallback: Convert extracted text to PDF for {filename}")
        try:
            text = self.extract_text(file_data, filename)
            if text and len(text.strip()) > 0:
                txt_filename = "fallback.txt"
                txt_path = os.path.join(temp_dir, txt_filename)
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
                
                # Convert text to PDF
                cmd_fallback = [
                    soffice_cmd,
                    "--headless",
                    "--nologo",
                    "--nofirststartwizard",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    temp_dir,
                    f"-env:UserInstallation=file://{temp_dir}/LibreOffice_User",
                    txt_path,
                ]
                
                subprocess.run(
                    cmd_fallback,
                    check=True,
                    timeout=30,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                
                fallback_pdf = os.path.join(temp_dir, "fallback.pdf")
                if os.path.exists(fallback_pdf):
                    logger.info(f"[FILE] Fallback successful: Generated PDF from text for {filename}")
                    with open(fallback_pdf, "rb") as f:
                        return f.read()
            else:
                logger.warning(f"[FILE] Fallback failed: Extracted text is empty for {filename}")
        except Exception as e:
            logger.error(f"[FILE] Fallback failed: {e}")
        
        return None

    def convert_to_pdf(self, file_data: bytes, filename: str) -> Optional[bytes]:
        """
        Converts Office documents (HWP, DOCX, XLSX, PPTX) to PDF using LibreOffice.
        HWP files are first converted to ODT using pyhwp, then to PDF.
        """
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if ext == "pdf":
            return file_data

        soffice_cmd = self._get_soffice_command()
        
        # Create a temporary directory for the conversion
        with tempfile.TemporaryDirectory() as temp_dir:
            # Set HOME to temp_dir for LibreOffice (required in some Docker/Server environments)
            env = os.environ.copy()
            env["HOME"] = temp_dir
            
            # For HWP files, use multi-layered strategy:
            # 1. Polaris Office (Web Automation) -> JPG
            # 2. hwp5html -> HTML -> PNG
            # 3. Text Extraction -> PDF
            if ext == "hwp":
                logger.info(f"[FILE] Starting HWP conversion for {filename}")
                
                # Save input file
                input_path = os.path.join(temp_dir, "input.hwp")
                with open(input_path, "wb") as f:
                    f.write(file_data)
                
                # Priority 1: Polaris Office
                try:
                    logger.info(f"[FILE] Attempting Polaris Office conversion for {filename}")
                    jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
                    if jpg_files:
                        logger.info(f"[FILE] Polaris conversion successful: {len(jpg_files)} images")
                        # Convert JPGs to single PDF
                        return self._images_to_pdf(jpg_files)
                    else:
                        logger.warning(f"[FILE] Polaris conversion failed, trying fallback")
                except Exception as e:
                    logger.error(f"[FILE] Polaris conversion error: {e}")

                # Priority 2: hwp5html
                logger.info(f"[FILE] Attempting hwp5html conversion for {filename}")
                png_files = self._convert_hwp_to_png_via_html(file_data, filename, temp_dir)
                if png_files:
                    logger.info(f"[FILE] hwp5html conversion successful: {len(png_files)} images")
                    return self._images_to_pdf(png_files)
                
                # Priority 3: Text Extraction Fallback
                logger.warning(f"[FILE] hwp5html failed, trying text extraction fallback")
                if soffice_cmd:
                    return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
                else:
                    logger.warning("[FILE] LibreOffice not found, cannot perform text fallback")
                    return None

            # For HWPX files:
            # 1. Polaris Office
            # 2. Text Extraction -> PDF
            elif ext == "hwpx":
                logger.info(f"[FILE] Starting HWPX conversion for {filename}")
                
                # Save input file
                input_path = os.path.join(temp_dir, "input.hwpx")
                with open(input_path, "wb") as f:
                    f.write(file_data)
                
                # Validate HWPX
                if not zipfile.is_zipfile(input_path):
                    logger.error(f"[FILE] Invalid HWPX file (not a zip): {filename}")
                    return None
                
                # Priority 1: Polaris Office
                try:
                    logger.info(f"[FILE] Attempting Polaris Office conversion for {filename}")
                    jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
                    if jpg_files:
                        logger.info(f"[FILE] Polaris conversion successful: {len(jpg_files)} images")
                        return self._images_to_pdf(jpg_files)
                    else:
                        logger.warning(f"[FILE] Polaris conversion failed, trying fallback")
                except Exception as e:
                    logger.error(f"[FILE] Polaris conversion error: {e}")

                # Priority 2: Text Extraction Fallback
                logger.warning(f"[FILE] Polaris failed, trying text extraction fallback")
                if soffice_cmd:
                    return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
                else:
                    logger.warning("[FILE] LibreOffice not found, cannot perform text fallback")
                    return None

            else:
                # Direct conversion for DOCX, XLSX, etc.
                if not soffice_cmd:
                    logger.warning(f"[FILE] LibreOffice not found. Skipping PDF conversion for {filename}.")
                    return None

                safe_filename = f"input.{ext}"
                input_path = os.path.join(temp_dir, safe_filename)
                
                with open(input_path, "wb") as f:
                    f.write(file_data)
            
            # Run soffice to convert to PDF
            cmd = [
                soffice_cmd,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                "--invisible",  # Additional flag for better headless operation
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                f"-env:UserInstallation=file://{temp_dir}/LibreOffice_User",
                input_path,
            ]

            try:
                # Log LibreOffice version for debugging
                version_check = subprocess.run([soffice_cmd, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logger.info(f"[FILE] LibreOffice Version: {version_check.stdout.decode().strip()}")
            except Exception:
                logger.warning("[FILE] Could not determine LibreOffice version")

            try:
                # Run conversion
                logger.info(f"[FILE] Converting {safe_filename} to PDF...")
                result = subprocess.run(
                    cmd,
                    check=True,
                    timeout=60,  # 60s timeout for conversion
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )

                # Output filename will be input.pdf
                pdf_filename = "input.pdf"
                pdf_path = os.path.join(temp_dir, pdf_filename)

                if os.path.exists(pdf_path):
                    logger.info(f"[FILE] PDF conversion successful for {filename}")
                    with open(pdf_path, "rb") as f:
                        return f.read()
                else:
                    logger.warning(
                        f"[FILE] PDF conversion failed: Output file not found for {filename} (temp: {pdf_filename})"
                    )
                    logger.warning(f"[FILE] STDOUT: {result.stdout.decode() if result.stdout else ''}")
                    logger.warning(f"[FILE] STDERR: {result.stderr.decode() if result.stderr else ''}")
                    
                    # Debug: List files in temp dir
                    try:
                        files = os.listdir(temp_dir)
                        logger.warning(f"[FILE] Files in temp dir: {files}")
                    except:
                        pass

                    # For HWP files, try fallback if ODT→PDF failed
                    if ext == "hwp":
                        logger.warning(f"[FILE] ODT→PDF failed, trying text extraction fallback")
                        return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)
                    
                    # Fallback for HWP/DOCX/XLSX/HWPX: Convert extracted text to PDF
                    if ext in ["hwp", "docx", "xlsx", "hwpx"]:
                        return self._fallback_text_to_pdf(file_data, filename, temp_dir, env, soffice_cmd)

                    return None
            except subprocess.TimeoutExpired:
                logger.error(f"[FILE] PDF conversion timed out for {filename}")
                return None
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"[FILE] PDF conversion error for {filename}: {e.stderr.decode() if e.stderr else str(e)}"
                )
                return None
            except Exception as e:
                logger.error(f"[FILE] Unexpected error during conversion: {e}")
                return None

    def _images_to_pdf(self, image_paths: List[str]) -> Optional[bytes]:
        """Converts a list of images to a single PDF."""
        try:
            from PIL import Image
            
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

    def _convert_hwp_to_png_via_html(self, file_data: bytes, filename: str, temp_dir: str) -> List[str]:
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
            logger.info(f"[FILE] Converting HWP to HTML using hwp5html...")
            
            # Check if hwp5html is available
            if not shutil.which("hwp5html"):
                logger.warning("[FILE] hwp5html not found in PATH")
                return []
            
            # Convert HWP to HTML
            # hwp5html creates a directory with index.html and resources
            cmd = ["hwp5html", hwp_path]
            
            result = subprocess.run(
                cmd,
                timeout=60,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir
            )
            
            # Debug: Log hwp5html output
            if result.stdout:
                logger.info(f"[FILE] hwp5html STDOUT: {result.stdout.decode()[:500]}")
            if result.stderr:
                stderr = result.stderr.decode()
                if stderr:
                    logger.warning(f"[FILE] hwp5html STDERR: {stderr[:500]}")
            
            # Debug: List all files created
            logger.info(f"[FILE] Files in temp_dir after hwp5html: {os.listdir(temp_dir)}")
            
            # hwp5html creates 'input' directory containing the XHTML (not HTML)
            html_dir = os.path.join(temp_dir, "input")
            
            # Debug: Check if input directory exists and list its contents
            if os.path.exists(html_dir) and os.path.isdir(html_dir):
                input_contents = os.listdir(html_dir)
                logger.info(f"[FILE] Contents of 'input' directory: {input_contents}")
            else:
                logger.warning(f"[FILE] 'input' directory not found at {html_dir}")
            
            # hwp5html generates index.xhtml, not index.html
            index_html = os.path.join(html_dir, "index.xhtml")
            
            # Also check if HTML was created in temp_dir directly
            if not os.path.exists(index_html):
                # Try alternative locations
                possible_locations = [
                    os.path.join(temp_dir, "index.html"),
                    os.path.join(temp_dir, "input.html"),
                ]
                
                # Also check in subdirectories
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    if os.path.isdir(item_path):
                        possible_html = os.path.join(item_path, "index.html")
                        if os.path.exists(possible_html):
                            index_html = possible_html
                            logger.info(f"[FILE] Found HTML at: {index_html}")
                            break
                        possible_locations.append(possible_html)
                
                # Check all possible locations
                for loc in possible_locations:
                    if os.path.exists(loc):
                        index_html = loc
                        logger.info(f"[FILE] Found HTML at: {index_html}")
                        break
            
            if not os.path.exists(index_html):
                logger.warning(f"[FILE] HTML file not found at {index_html}")
                logger.warning(f"[FILE] Exit code: {result.returncode}")
                return []
            
            logger.info(f"[FILE] HWP→HTML conversion successful")
            
            # Step 2: HTML → Full-page Screenshot, then split by height
            logger.info(f"[FILE] Rendering HTML to full-page screenshot using Playwright...")
            
            try:
                # Use subprocess to avoid asyncio event loop conflicts
                output_png = os.path.join(temp_dir, "fullpage.png")
                
                # Create a simple Python script to run Playwright
                # Full page screenshot to capture ALL content
                script_content = f'''
from playwright.sync_api import sync_playwright
import sys

html_path = r"{os.path.abspath(index_html).replace(os.sep, '/')}"
output_path = r"{output_png}"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Set viewport width to 820px (approx A4 width) to minimize margins
        # full_page=True will capture the entire height, so bottom won't be cut off
        page = browser.new_page(viewport={{"width": 820, "height": 1200}})
        page.goto(f"file:///{{html_path}}")
        page.wait_for_timeout(1000)
        
        # Take FULL PAGE screenshot (entire scrollable content)
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
                    cwd=temp_dir
                )
                
                if os.path.exists(output_png) and os.path.getsize(output_png) > 0:
                    logger.info(f"[FILE] Full-page screenshot successful")
                    
                    # Split the long screenshot into page-sized chunks
                    png_files = []
                    
                    try:
                        from PIL import Image
                        
                        img = Image.open(output_png)
                        img_width, img_height = img.size
                        
                        logger.info(f"[FILE] Full image size: {img_width}x{img_height}")
                        
                        # Calculate number of pages based on A4 ratio (height = width * 1.414)
                        # This ensures we split equally based on the content length
                        a4_ratio = 1.414
                        expected_page_height = int(img_width * a4_ratio)
                        
                        # Calculate number of pages (rounding to nearest integer)
                        num_pages = max(1, round(img_height / expected_page_height))
                        
                        # Calculate exact height per page to split equally
                        page_height = img_height // num_pages
                        
                        logger.info(f"[FILE] Splitting into {num_pages} pages (approx {page_height}px each)")
                        
                        for page_num in range(num_pages):
                            y_start = page_num * page_height
                            # For the last page, take the rest (though it should be exact with //)
                            if page_num == num_pages - 1:
                                y_end = img_height
                            else:
                                y_end = (page_num + 1) * page_height
                            
                            # Crop the page
                            page_img = img.crop((0, y_start, img_width, y_end))
                            
                            # Save as PNG
                            png_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")
                            page_img.save(png_path)
                            png_files.append(png_path)
                            logger.info(f"[FILE] Generated page {page_num + 1}/{num_pages} (height: {y_end - y_start}px)")
                        
                        img.close()
                        
                    except Exception as split_error:
                        logger.error(f"[FILE] Image splitting error: {split_error}")
                        return []
                    
                    return png_files
                else:
                    logger.warning(f"[FILE] Screenshot file not created or empty")
                    if result.stderr:
                        stderr = result.stderr.decode()
                        logger.warning(f"[FILE] Playwright STDERR: {stderr[:500]}")
                    return []
                
            except subprocess.TimeoutExpired:
                logger.error("[FILE] Playwright rendering timed out")
                return []
            except Exception as e:
                logger.error(f"[FILE] Playwright rendering error: {e}")
                return []
                
        except subprocess.TimeoutExpired:
            logger.error(f"[FILE] HWP→HTML conversion timed out")
            return []
        except Exception as e:
            logger.error(f"[FILE] HWP→HTML conversion error: {e}")
            return []

    def _convert_xlsx_to_png_via_html(self, file_data: bytes, filename: str, temp_dir: str) -> List[str]:
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
            logger.info(f"[FILE] Converting XLSX to HTML using xlsx2html...")
            
            try:
                from xlsx2html import xlsx2html
                html_path = os.path.join(temp_dir, "index.html")
                xlsx2html(xlsx_path, html_path)
                
                if not os.path.exists(html_path):
                    logger.warning(f"[FILE] HTML file not created by xlsx2html")
                    return []
                    
                logger.info(f"[FILE] XLSX→HTML conversion successful")
                
            except ImportError:
                logger.error("[FILE] xlsx2html not installed")
                return []
            except Exception as e:
                logger.error(f"[FILE] xlsx2html conversion error: {e}")
                return []

            # Step 2: HTML → Full-page Screenshot using Playwright
            logger.info(f"[FILE] Rendering HTML to full-page screenshot using Playwright...")
            
            try:
                # Use subprocess to avoid asyncio event loop conflicts
                output_png = os.path.join(temp_dir, "fullpage.png")
                
                # Create a simple Python script to run Playwright
                script_content = f'''
from playwright.sync_api import sync_playwright
import sys
import os

html_path = r"{os.path.abspath(html_path).replace(os.sep, '/')}"
output_path = r"{output_png}"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Open HTML file
        page.goto(f"file:///{{html_path}}")
        page.wait_for_timeout(1000)
        
        # Dynamic Viewport Sizing
        # Get actual content size
        body_handle = page.query_selector("body")
        if body_handle:
            box = body_handle.bounding_box()
            if box:
                width = int(box['width']) + 50
                height = int(box['height']) + 50
                # Set viewport to match content size (plus margin)
                page.set_viewport_size({{"width": width, "height": height}})
        
        # Take FULL PAGE screenshot
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
                
                # Run the script in a subprocess
                result = subprocess.run(
                    ["python", script_path],
                    timeout=30,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=temp_dir
                )
                
                if os.path.exists(output_png) and os.path.getsize(output_png) > 0:
                    logger.info(f"[FILE] Full-page screenshot successful")
                    
                    # Split the long screenshot into page-sized chunks
                    png_files = []
                    
                    try:
                        from PIL import Image
                        
                        img = Image.open(output_png)
                        img_width, img_height = img.size
                        
                        logger.info(f"[FILE] Full image size: {img_width}x{img_height}")
                        
                        # Calculate number of pages based on A4 ratio (height = width * 1.414)
                        a4_ratio = 1.414
                        expected_page_height = int(img_width * a4_ratio)
                        
                        # Ensure minimum height to avoid too many small pages for wide excel sheets
                        expected_page_height = max(expected_page_height, 1000)
                        
                        # Calculate number of pages
                        num_pages = max(1, round(img_height / expected_page_height))
                        
                        # Calculate exact height per page
                        page_height = img_height // num_pages
                        
                        logger.info(f"[FILE] Splitting into {num_pages} pages (approx {page_height}px each)")
                        
                        for page_num in range(num_pages):
                            y_start = page_num * page_height
                            if page_num == num_pages - 1:
                                y_end = img_height
                            else:
                                y_end = (page_num + 1) * page_height
                            
                            # Crop the page
                            page_img = img.crop((0, y_start, img_width, y_end))
                            
                            # Save as PNG
                            png_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")
                            page_img.save(png_path)
                            png_files.append(png_path)
                        
                        img.close()
                        
                    except Exception as split_error:
                        logger.error(f"[FILE] Image splitting error: {split_error}")
                        return []
                    
                    return png_files
                else:
                    logger.warning(f"[FILE] Screenshot file not created or empty")
                    if result.stderr:
                        stderr = result.stderr.decode()
                        logger.warning(f"[FILE] Playwright STDERR: {stderr[:500]}")
                    return []
                
            except subprocess.TimeoutExpired:
                logger.error("[FILE] Playwright rendering timed out")
                return []
            except Exception as e:
                logger.error(f"[FILE] Playwright rendering error: {e}")
                return []

        except Exception as e:
            logger.error(f"[FILE] HWP→HTML conversion error: {e}")
            return []

    def _convert_xlsx_to_png_via_html(self, file_data: bytes, filename: str, temp_dir: str) -> List[str]:
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
            logger.info(f"[FILE] Converting XLSX to HTML using xlsx2html...")
            
            try:
                from xlsx2html import xlsx2html
                html_path = os.path.join(temp_dir, "index.html")
                xlsx2html(xlsx_path, html_path)
                
                if not os.path.exists(html_path):
                    logger.warning(f"[FILE] HTML file not created by xlsx2html")
                    return []
                    
                logger.info(f"[FILE] XLSX→HTML conversion successful")
                
            except ImportError:
                logger.error("[FILE] xlsx2html not installed")
                return []
            except Exception as e:
                logger.error(f"[FILE] xlsx2html conversion error: {e}")
                return []

            # Step 2: HTML → Full-page Screenshot using Playwright
            logger.info(f"[FILE] Rendering HTML to full-page screenshot using Playwright...")
            
            try:
                # Use subprocess to avoid asyncio event loop conflicts
                output_png = os.path.join(temp_dir, "fullpage.png")
                
                # Create a simple Python script to run Playwright
                script_content = f'''
from playwright.sync_api import sync_playwright
import sys
import os

html_path = r"{os.path.abspath(html_path).replace(os.sep, '/')}"
output_path = r"{output_png}"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Open HTML file
        page.goto(f"file:///{{html_path}}")
        page.wait_for_timeout(1000)
        
        # Dynamic Viewport Sizing
        # Get actual content size
        body_handle = page.query_selector("body")
        if body_handle:
            box = body_handle.bounding_box()
            if box:
                width = int(box['width']) + 50
                height = int(box['height']) + 50
                # Set viewport to match content size (plus margin)
                page.set_viewport_size({{"width": width, "height": height}})
        
        # Take FULL PAGE screenshot
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
                
                # Run the script in a subprocess
                result = subprocess.run(
                    ["python", script_path],
                    timeout=30,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=temp_dir
                )
                
                if os.path.exists(output_png) and os.path.getsize(output_png) > 0:
                    logger.info(f"[FILE] Full-page screenshot successful")
                    
                    # Split the long screenshot into page-sized chunks
                    png_files = []
                    
                    try:
                        from PIL import Image
                        
                        img = Image.open(output_png)
                        img_width, img_height = img.size
                        
                        logger.info(f"[FILE] Full image size: {img_width}x{img_height}")
                        # Calculate number of pages based on A4 ratio (height = width * 1.414)
                        a4_ratio = 1.414
                        expected_page_height = int(img_width * a4_ratio)
                        
                        # Ensure minimum height to avoid too many small pages for wide excel sheets
                        expected_page_height = max(expected_page_height, 1000)
                        
                        # Calculate number of pages
                        num_pages = max(1, round(img_height / expected_page_height))
                        
                        # Calculate exact height per page
                        page_height = img_height // num_pages
                        
                        logger.info(f"[FILE] Splitting into {num_pages} pages (approx {page_height}px each)")
                        
                        for page_num in range(num_pages):
                            y_start = page_num * page_height
                            if page_num == num_pages - 1:
                                y_end = img_height
                            else:
                                y_end = (page_num + 1) * page_height
                            
                            # Crop the page
                            page_img = img.crop((0, y_start, img_width, y_end))
                            
                            # Save as PNG
                            png_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")
                            page_img.save(png_path)
                            png_files.append(png_path)
                        
                        img.close()
                        
                    except Exception as split_error:
                        logger.error(f"[FILE] Image splitting error: {split_error}")
                        return []
                    
                    return png_files
                else:
                    logger.warning(f"[FILE] Screenshot file not created or empty")
                    if result.stderr:
                        stderr = result.stderr.decode()
                        logger.warning(f"[FILE] Playwright STDERR: {stderr[:500]}")
                    return []
                
            except subprocess.TimeoutExpired:
                logger.error("[FILE] Playwright rendering timed out")
                return []
            except Exception as e:
                logger.error(f"[FILE] Playwright rendering error: {e}")
                return []

        except Exception as e:
            logger.error(f"[FILE] XLSX→HTML conversion error: {e}")
            return []

    def _process_png_files(self, png_files: List[str], max_pages: int) -> List[bytes]:
        """
        Process a list of PNG files: resize, convert to JPEG, and add watermark.
        """
        preview_images = []
        num_images = min(len(png_files), max_pages)
        
        for png_path in png_files[:num_images]:
            try:
                from PIL import Image
                
                with Image.open(png_path) as img:
                    # Convert to RGB if needed
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    
                    # Resize if too large
                    max_width = 1024
                    if img.width > max_width:
                        ratio = max_width / float(img.width)
                        new_height = int(img.height * ratio)
                        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Save as JPEG
                    img_buffer = io.BytesIO()
                    img.save(img_buffer, format="JPEG", quality=85)
                    img_buffer.seek(0)
                    
                    # Add watermark
                    watermarked = self.add_watermark(img_buffer.getvalue())
                    preview_images.append(watermarked)
            except Exception as e:
                logger.warning(f"[FILE] Failed to process PNG {png_path}: {e}")
                continue
        
        return preview_images

    def generate_preview_images(
        self, file_data: bytes, filename: str, max_pages: int = 100
    ) -> List[bytes]:
        """
        Generates preview images (up to max_pages) for PDF and Office documents.
        For HWP files, uses direct PNG conversion for better format preservation.
        Returns list of bytes of JPEG images.
        """
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        supported_exts = [
            "pdf",
            "hwp",
            "hwpx",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "ppt",
            "pptx",
        ]

        if ext not in supported_exts:
            return []

        try:
            # Special handling for HWP: Polaris -> hwp5html -> PDF Fallback
            if ext == "hwp":
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Priority 1: Polaris Office
                    try:
                        logger.info(f"[FILE] Attempting Polaris Office conversion for {filename}")
                        # Save input file
                        input_path = os.path.join(temp_dir, "input.hwp")
                        with open(input_path, "wb") as f:
                            f.write(file_data)
                            
                        jpg_files = self.polaris_service.convert_to_jpg(input_path, temp_dir)
                        if jpg_files:
                            logger.info(f"[FILE] Polaris conversion successful: {len(jpg_files)} images")
                            return self._process_png_files(jpg_files, max_pages)
                    except Exception as e:
                        logger.error(f"[FILE] Polaris conversion error: {e}")

                    # Priority 2: hwp5html
                    png_files = self._convert_hwp_to_png_via_html(file_data, filename, temp_dir)
                    
                    if not png_files:
                        # Fallback to PDF conversion if PNG fails
                        logger.warning(f"[FILE] PNG conversion failed, trying PDF fallback")
                        return self._generate_via_pdf(file_data, filename, max_pages)
                    
                    # Process PNG files
                    preview_images = self._process_png_files(png_files, max_pages)
                    
                    if preview_images:
                        logger.info(f"[FILE] Generated {len(preview_images)} preview images from HWP")
                        return preview_images
                    else:
                        logger.warning(f"[FILE] No valid preview images generated from PNG files")
                        return []

            # Special handling for XLSX/XLS: xlsx2html + Playwright conversion
            if ext in ["xlsx", "xls"]:
                with tempfile.TemporaryDirectory() as temp_dir:
                    png_files = self._convert_xlsx_to_png_via_html(file_data, filename, temp_dir)
                    
                    if png_files:
                        # Process PNG files
                        preview_images = self._process_png_files(png_files, max_pages)
                        
                        if preview_images:
                            logger.info(f"[FILE] Generated {len(preview_images)} preview images from XLSX")
                            return preview_images
                    
                    # Fallback to PDF conversion if PNG fails
                    logger.warning(f"[FILE] XLSX PNG conversion failed, trying PDF fallback")
                    # Continue to PDF conversion below

            # For other formats, use PDF conversion
            return self._generate_via_pdf(file_data, filename, max_pages)

        except Exception as e:
            logger.warning(f"[FILE] Preview generation failed for {filename}: {e}")
            return []

    def _generate_via_pdf(self, file_data: bytes, filename: str, max_pages: int = 5) -> List[bytes]:
        """
        Generate preview images via PDF conversion (for non-HWP files or fallback).
        """
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        
        try:
            # 1. Convert to PDF if needed
            pdf_data = file_data
            if ext != "pdf":
                pdf_data = self.convert_to_pdf(file_data, filename)
                if not pdf_data:
                    logger.warning(f"[FILE] PDF conversion returned None for {filename}")
                    return []

            # 2. Render PDF to Images using PyMuPDF (fitz)
            doc = fitz.open(stream=pdf_data, filetype="pdf")
            logger.info(f"[FILE] Rendering PDF preview for {filename} (pages: {doc.page_count})")
            
            preview_images = []

            # Limit pages
            num_pages = min(doc.page_count, max_pages)

            for i in range(num_pages):
                page = doc.load_page(i)

                # Render at 2x zoom for better quality
                zoom = 2.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)

                # Convert to PIL Image for resizing and watermarking
                from PIL import Image

                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                # Optimization: Resize if too large (max width 1024px)
                max_width = 1024
                if img.width > max_width:
                    ratio = max_width / float(img.width)
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                # Save to memory buffer as JPEG
                img_buffer = io.BytesIO()
                img.save(img_buffer, format="JPEG", quality=85)
                img_buffer.seek(0)

                # Add Watermark
                watermarked = self.add_watermark(img_buffer.getvalue())
                preview_images.append(watermarked)

            return preview_images

        except Exception as e:
            logger.warning(f"[FILE] Preview generation failed for {filename}: {e}")
            return []

    def add_watermark(self, image_bytes: bytes, text: str = "PREVIEW") -> bytes:
        """
        Adds a semi-transparent watermark to the center of the image.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            with Image.open(io.BytesIO(image_bytes)) as base:
                # Make the image editable
                txt = Image.new("RGBA", base.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(txt)

                # Calculate font size based on image width (e.g., 15% of width)
                fontsize = int(base.width / 6)
                try:
                    # Try to load a default font, otherwise use default
                    # On Windows, arial.ttf is usually available
                    font = ImageFont.truetype("arial.ttf", fontsize)
                except:
                    font = ImageFont.load_default()

                # Calculate text size and position
                # textbbox is available in Pillow >= 8.0.0
                try:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                except AttributeError:
                    # Fallback for older Pillow
                    text_width, text_height = draw.textsize(text, font=font)

                x = (base.width - text_width) / 2
                y = (base.height - text_height) / 2

                # Draw text with transparency (RGBA)
                # White text with 50% opacity
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 128))

                # Outline (Black with 50% opacity) for better visibility
                stroke_width = 2
                draw.text((x - stroke_width, y), text, font=font, fill=(0, 0, 0, 128))
                draw.text((x + stroke_width, y), text, font=font, fill=(0, 0, 0, 128))
                draw.text((x, y - stroke_width), text, font=font, fill=(0, 0, 0, 128))
                draw.text((x, y + stroke_width), text, font=font, fill=(0, 0, 0, 128))

                # Composite
                out = Image.alpha_composite(base.convert("RGBA"), txt)

                # Save back to bytes
                out_buffer = io.BytesIO()
                out.convert("RGB").save(out_buffer, format="JPEG", quality=85)
                return out_buffer.getvalue()

        except ImportError:
            logger.warning("[FILE] Pillow not installed. Skipping watermark.")
            return image_bytes
        except Exception as e:
            logger.error(f"[FILE] Watermark failed: {e}")
            return image_bytes

    def is_pdf(self, filename: str) -> bool:
        return filename.lower().endswith(".pdf")

    def is_image(self, filename: str) -> bool:
        return filename.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        )

    def validate_file_size(self, file_data: bytes, max_mb: int) -> bool:
        return len(file_data) <= max_mb * 1024 * 1024

    def extract_filename(self, url: str) -> str:
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        filename = path.split("/")[-1]

        # If filename is empty or generic, try query params
        if not filename or "." not in filename:
            qs = urllib.parse.parse_qs(parsed.query)
            if "file" in qs:
                filename = qs["file"][0]
            elif "filename" in qs:
                filename = qs["filename"][0]

        return urllib.parse.unquote(filename)

    def sanitize_filename(self, filename: str) -> str:
        import re

        # Remove directory traversal
        filename = re.sub(r"[/\\]", "", filename)
        # Remove ..
        filename = filename.replace("..", "")
        # Remove control characters
        filename = re.sub(r"[\x00-\x1f]", "", filename)
        return filename
