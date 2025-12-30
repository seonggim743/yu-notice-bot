"""
Image processing utilities.
"""
import io
import logging
from typing import List

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class ImageHandler:
    """Handles image processing operations like watermarking and PNG processing."""

    def images_to_pdf(self, image_paths: List[str]) -> bytes:
        """Converts a list of images to a single PDF."""
        try:
            doc = fitz.open()

            for img_path in image_paths:
                img = fitz.open(img_path)
                pdfbytes = img.convert_to_pdf()
                img.close()

                img_doc = fitz.open("pdf", pdfbytes)
                doc.insert_pdf(img_doc)
                img_doc.close()

            pdf_bytes = doc.tobytes()
            doc.close()
            return pdf_bytes
        except Exception as e:
            logger.error(f"[FILE] Image to PDF conversion failed: {e}")
            return None

    def process_png_files(
        self, png_files: List[str], max_pages: int, add_watermark_func
    ) -> List[bytes]:
        """
        Process a list of PNG files: resize, convert to JPEG, and add watermark.
        """
        from PIL import Image

        preview_images = []
        num_images = min(len(png_files), max_pages)

        for png_path in png_files[:num_images]:
            try:
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
                    watermarked = add_watermark_func(img_buffer.getvalue())
                    preview_images.append(watermarked)
            except Exception as e:
                logger.warning(f"[FILE] Failed to process PNG {png_path}: {e}")
                continue

        return preview_images

    def add_watermark(self, image_bytes: bytes, text: str = "PREVIEW") -> bytes:
        """
        Adds a semi-transparent watermark to the center of the image.
        DISABLED: Now simply returns the original image.
        """
        return image_bytes

    def optimize_for_telegram(self, image_bytes: bytes) -> bytes:
        """
        Optimizes image for Telegram:
        1. Ensures width + height <= 10000 pixels.
        2. Converts to RGB/JPEG format for compatibility.
        """
        from PIL import Image
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                # 1. Check content type/mode
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                width, height = img.size
                
                # Telegram Limit: width + height <= 10000
                if width + height > 10000:
                    # Calculate new size
                    # Conservative factor to be safe
                    target_sum = 9000
                    ratio = target_sum / float(width + height)
                    
                    new_width = int(width * ratio)
                    new_height = int(height * ratio)
                    
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    logger.info(f"[IMAGE] Resized for Telegram: {width}x{height} -> {new_width}x{new_height}")
                
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)
                output.seek(0)
                return output.getvalue()
                
        except Exception as e:
            logger.error(f"[IMAGE] Optimization failed: {e}")
            return image_bytes

