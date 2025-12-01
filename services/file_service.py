import aiohttp
import io
import zlib
import olefile
import struct
import logging
from typing import Optional, Tuple
from pypdf import PdfReader
from pdf2image import convert_from_bytes

logger = logging.getLogger(__name__)

class FileService:
    def __init__(self):
        pass

    async def download_file(self, session: aiohttp.ClientSession, url: str, headers: dict = None) -> Optional[bytes]:
        """Downloads a file into memory."""
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
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
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        
        try:
            if ext == 'pdf':
                text = self._extract_pdf_text(file_data)
            elif ext == 'hwp':
                text = self._extract_hwp_text(file_data)
            elif ext == 'hwpx':
                text = self._extract_hwpx_text(file_data)
            else:
                text = ""
            
            # Sanitize: Remove null bytes
            if text:
                text = text.replace('\x00', '')
                
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
                    section_text = decompressed.decode('utf-16-le', errors='ignore')
                    
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
                        if (0xAC00 <= code <= 0xD7A3) or \
                           (0x0020 <= code <= 0x007E) or \
                           (code == 0x000A) or (code == 0x0009) or \
                           (0x3130 <= code <= 0x318F) or \
                           (0x1100 <= code <= 0x11FF):
                            cleaned_text += char
                        else:
                            # Replace unknown/control chars with space if they are not just nulls
                            if code > 0x001F: 
                                cleaned_text += " "
                    
                    # Collapse multiple spaces
                    import re
                    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
                    
                    if len(cleaned_text) > 5: # Ignore very short garbage sections
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
                    if name.startswith('Contents/section') and name.endswith('.xml'):
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

    def generate_preview_image(self, file_data: bytes, filename: str) -> Optional[bytes]:
        """
        Generates a preview image (first page) for PDF.
        Returns bytes of JPEG image.
        """
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        
        if ext != 'pdf':
            return None
            
        try:
            # Convert first page only
            # fmt='jpeg' and grayscale=False reduces size
            images = convert_from_bytes(file_data, first_page=1, last_page=1, fmt='jpeg')
            
            if not images:
                return None

            image = images[0]
            
            # Optimization: Resize if too large (max width 1024px)
            max_width = 1024
            if image.width > max_width:
                ratio = max_width / float(image.width)
                new_height = int(image.height * ratio)
                image = image.resize((max_width, new_height))

            # Save to memory buffer as JPEG
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='JPEG', quality=85)
            img_buffer.seek(0)
            
            return img_buffer.getvalue()
            
        except Exception as e:
            error_msg = str(e)
            if "poppler" in error_msg.lower() or "pdfinfonotinstallederror" in error_msg.lower():
                logger.warning(f"[FILE] Poppler not found. Skipping PDF preview for {filename}. (Expected on Windows without manual install)")
            else:
                logger.warning(f"[FILE] Preview generation failed for {filename}: {e}")
            return None
