"""
Protocol-based interfaces for Dependency Injection.
These interfaces define contracts for services, enabling easier testing and extensibility.
"""
from typing import Protocol, Optional, List, Dict, Any, runtime_checkable
import aiohttp

from models.notice import Notice


@runtime_checkable
class INotificationService(Protocol):
    """Interface for notification services."""

    async def send_telegram(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_message_id: Optional[int] = None,
        changes: Optional[Dict] = None,
    ) -> Optional[int]:
        """Sends a notice to Telegram. Returns message ID if successful."""
        ...

    async def send_discord(
        self,
        session: aiohttp.ClientSession,
        notice: Notice,
        is_new: bool,
        modified_reason: str = "",
        existing_thread_id: str = None,
        changes: Optional[Dict] = None,
    ) -> Optional[str]:
        """Sends a notice to Discord. Returns thread/message ID if successful."""
        ...

    async def send_menu_notification(
        self, session: aiohttp.ClientSession, notice: Notice, menu_data: Dict[str, Any]
    ) -> None:
        """Sends menu notification to Telegram."""
        ...

    def generate_clean_diff(self, old_text: str, new_text: str) -> str:
        """Generates a clean diff between two texts."""
        ...


@runtime_checkable
class IFileService(Protocol):
    """Interface for file handling services."""

    async def download_file(
        self, session: aiohttp.ClientSession, url: str, headers: dict = None
    ) -> Optional[bytes]:
        """Downloads a file into memory."""
        ...

    def extract_text(self, file_data: bytes, filename: str) -> str:
        """Extracts text from PDF, HWP, or Office files."""
        ...

    def convert_to_pdf(self, file_data: bytes, filename: str) -> Optional[bytes]:
        """Converts Office documents to PDF."""
        ...

    def generate_preview_images(
        self, file_data: bytes, filename: str, max_pages: int = 100
    ) -> List[bytes]:
        """Generates preview images for documents."""
        ...

    def is_pdf(self, filename: str) -> bool:
        """Checks if file is a PDF."""
        ...

    def is_image(self, filename: str) -> bool:
        """Checks if file is an image."""
        ...

    def validate_file_size(self, file_data: bytes, max_mb: int) -> bool:
        """Validates file size against limit."""
        ...


@runtime_checkable
class INoticeRepository(Protocol):
    """Interface for notice data repository."""

    async def get_notice(self, site_key: str, article_id: str) -> Optional[Notice]:
        """Gets a notice by site key and article ID."""
        ...

    async def save_notice(self, notice: Notice) -> bool:
        """Saves a notice to the repository."""
        ...

    async def notice_exists(self, site_key: str, article_id: str) -> bool:
        """Checks if a notice exists."""
        ...


@runtime_checkable
class IAIService(Protocol):
    """Interface for AI analysis services."""

    async def analyze_notice(
        self, text: str, site_key: str = "", attachments: List[Any] = None
    ) -> Dict[str, Any]:
        """Analyzes notice text and returns metadata."""
        ...

    async def get_embedding(self, text: str) -> List[float]:
        """Gets embedding vector for text."""
        ...

    async def get_diff_summary(self, old_text: str, new_text: str) -> str:
        """Generates a summary of differences."""
        ...

    async def extract_menu_from_image(
        self, image_url: str, image_data: bytes
    ) -> Dict[str, Any]:
        """Extracts menu data from an image."""
        ...
