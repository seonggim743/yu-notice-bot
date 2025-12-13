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
                    font = ImageFont.truetype("arial.ttf", fontsize)
                except:
                    font = ImageFont.load_default()

                # Calculate text size and position
                try:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                except AttributeError:
                    text_width, text_height = draw.textsize(text, font=font)

                x = (base.width - text_width) / 2
                y = (base.height - text_height) / 2

                # Draw text with transparency (RGBA)
                draw.text((x, y), text, font=font, fill=(255, 255, 255, 128))

                # Outline for better visibility
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
