"""
File service initialization.
Exports all file-related classes for easy importing.
"""

__all__ = ["BaseFileHandler", "PDFHandler", "HWPHandler", "OfficeHandler", "ImageHandler"]


def __getattr__(name):
    if name == "BaseFileHandler":
        from services.file.base import BaseFileHandler

        return BaseFileHandler
    if name == "PDFHandler":
        from services.file.pdf import PDFHandler

        return PDFHandler
    if name == "HWPHandler":
        from services.file.hwp import HWPHandler

        return HWPHandler
    if name == "OfficeHandler":
        from services.file.office import OfficeHandler

        return OfficeHandler
    if name == "ImageHandler":
        from services.file.image import ImageHandler

        return ImageHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
