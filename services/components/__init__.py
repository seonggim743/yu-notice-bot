"""
Components package for ScraperService decomposition.
Provides TargetManager, HashCalculator, ChangeDetector, and AttachmentProcessor.
"""
from services.components.target_manager import TargetManager
from services.components.hash_calculator import HashCalculator
from services.components.change_detector import ChangeDetector
from services.components.attachment_processor import AttachmentProcessor

__all__ = [
    "TargetManager",
    "HashCalculator",
    "ChangeDetector",
    "AttachmentProcessor",
]
