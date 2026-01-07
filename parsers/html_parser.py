from abc import ABC, abstractmethod
from typing import List, Optional
from bs4 import BeautifulSoup
import re
import urllib.parse
from models.notice import Notice, Attachment
from core.logger import get_logger

logger = get_logger(__name__)


class BaseParser(ABC):
    @abstractmethod
    def parse_list(self, html: str, site_key: str, base_url: str) -> List[Notice]:
        pass

    @abstractmethod
    def parse_detail(self, html: str, notice: Notice) -> Notice:
        pass


class HTMLParser(BaseParser):
    def __init__(
        self,
        list_selector: str,
        title_selector: str,
        link_selector: str,
        content_selector: str,
    ):
        self.list_selector = list_selector
        self.title_selector = title_selector
        self.link_selector = link_selector
        # Expanded selectors for YU sites
        self.content_selectors = [
            content_selector,
            ".b-content-box .fr-view",  # Froala editor content (most precise)
            ".b-content-box",
            ".fr-view",  # Froala editor (fallback)
            ".view-con",
            ".board-view-con",
            "#article_text",
            ".bbs_view",
            ".view_content",
            ".b-view-content",
        ]

    def parse_list(self, html: str, site_key: str, base_url: str) -> List[Notice]:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        rows = soup.select(self.list_selector)

        if not rows:
            logger.warning(
                f"[PARSER] No items found with selector '{self.list_selector}' for {site_key}"
            )

        for row in rows:
            try:
                title_el = row.select_one(self.title_selector)
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)

                # Clean up title - remove common markers (N=New, HOT, UP, etc.)
                # These appear at the end of titles on YU notice boards
                title = re.sub(r"\s*[NUHOT]+\s*$", "", title)
                title = re.sub(r"\s*New\s*$", "", title, flags=re.IGNORECASE)
                title = title.strip()

                # Link
                link_el = row.select_one(self.link_selector)
                href = link_el.get("href") if link_el else title_el.get("href")

                if not href:
                    continue

                # Skip obviously invalid links before URL joining
                if href.startswith(("tel:", "mailto:", "javascript:")):
                    logger.debug(f"[PARSER] Skipping invalid scheme: {href}")
                    continue

                full_url = urllib.parse.urljoin(base_url, href)
                parsed = urllib.parse.urlparse(full_url)

                # Skip hash-only links
                if parsed.fragment and not parsed.path:
                    logger.debug(f"[PARSER] Skipping hash-only link: {full_url}")
                    continue

                # Skip resource download links (images, PDFs, etc. without article ID)
                if any(
                    x in parsed.path.lower()
                    for x in ["resourcedown", "filedown", "download.do"]
                ):
                    if (
                        "articleno" not in parsed.query.lower()
                        and "seq" not in parsed.query.lower()
                    ):
                        logger.debug(
                            f"[PARSER] Skipping resource download link: {full_url}"
                        )
                        continue

                # Article ID Extraction (Generic approach: query param or path)
                qs = urllib.parse.parse_qs(parsed.query)

                # Common patterns for ID
                article_id = None
                if "articleNo" in qs:
                    article_id = qs["articleNo"][0]
                elif "seq" in qs:
                    article_id = qs["seq"][0]
                elif "id" in qs:
                    article_id = qs["id"][0]

                if not article_id:
                    # Only warn if it looks like a notice link (has .do extension or board in path)
                    if (
                        ".do" in parsed.path
                        or "board" in parsed.path.lower()
                        or "notice" in parsed.path.lower()
                    ):
                        logger.warning(
                            f"[PARSER] Could not extract article ID from URL: {full_url}"
                        )
                    continue

                items.append(
                    Notice(
                        site_key=site_key,
                        article_id=article_id,
                        title=title,
                        url=full_url,
                    )
                )
            except Exception as e:
                logger.warning(f"[PARSER] Failed to parse list item: {e}")
                continue

        logger.info(f"[PARSER] Found {len(items)} items for {site_key}")
        return items

    def parse_detail(self, html: str, notice: Notice) -> Notice:
        # 1. Extract Metadata (Date, Author)
        self._extract_metadata(html, notice)

        soup = BeautifulSoup(html, "html.parser")

        # 2. Extract Attachments
        self._extract_attachments(soup, notice)

        # 3. Extract Content
        self._extract_content(soup, notice)

        # 4. Extract Images
        self._extract_images(soup, notice)

        return notice

    def _extract_metadata(self, html: str, notice: Notice):
        """Extract date and author from raw HTML"""
        from datetime import datetime

        # Date patterns
        date_match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", html)
        if date_match:
            try:
                year, month, day, hour, minute = date_match.groups()
                notice.published_at = datetime(
                    int(year), int(month), int(day), int(hour), int(minute)
                )
                logger.info(f"[PARSER] Extracted published_at: {notice.published_at}")
            except Exception as e:
                logger.warning(f"[PARSER] Failed to parse date: {e}")

        # Author pattern
        author_match = re.search(
            r"작성자[^<]*</span>\s*<span[^>]*>([^<]+)</span>", html
        )
        if author_match:
            author = author_match.group(1).strip()
            if author and len(author) < 50 and not re.match(r"^\d{4}", author):
                notice.author = author
                logger.info(f"[PARSER] Extracted author: {notice.author}")

    def _extract_attachments(self, soup: BeautifulSoup, notice: Notice):
        """Extract attachments from soup"""
        files = []

        # 1. Try to find b-file-box container
        file_box = soup.select_one(".b-file-box")
        if file_box:
            files = file_box.select("a.b-file-dwn")
            logger.info(f"[PARSER] Found {len(files)} attachments in .b-file-box")

        # 2. Fallback: Try other common file list containers
        if not files:
            file_containers = soup.select(".b-file-list, .view-file, .file-list")
            for container in file_containers:
                files.extend(container.select("a"))
            if files:
                logger.info(
                    f"[PARSER] Found {len(files)} attachments in fallback containers"
                )

        # 3. Last resort: Find all links with file-related hrefs
        if not files:
            debug_links = soup.find_all("a", href=True)
            files = [
                link
                for link in debug_links
                if "fileDownload" in link.get("href", "")
                or "attachNo" in link.get("href", "")
            ]
            if files:
                logger.info(
                    f"[PARSER] Using fallback file detection, found {len(files)} links"
                )

        # Process found file links
        for f in files:
            href = f.get("href")
            if not href:
                continue

            # Get filename
            name = f.get_text(strip=True)

            # Clean up common Korean text artifacts
            if "다운로드" in name or "첨부파일" in name:
                parent = f.parent
                if parent:
                    parent_text = parent.get_text(strip=True)
                    for remove_text in ["첨부파일 다운로드", "다운로드", "첨부파일"]:
                        parent_text = parent_text.replace(remove_text, "")
                    parent_text = re.sub(
                        r"\(다운로드\s*:\s*\d+\)", "", parent_text
                    ).strip()
                    if parent_text and len(parent_text) > 3:
                        name = parent_text.strip()

            # Fallback: extract from URL
            if not name or name == href or len(name) < 3:
                name = href.split("/")[-1]
                from urllib.parse import unquote

                name = unquote(name)

            # Build full URL
            url = urllib.parse.urljoin(notice.url, href)

            # Avoid duplicates
            if not any(a.url == url for a in notice.attachments):
                notice.attachments.append(Attachment(name=name, url=url))
                logger.info(f"[PARSER] Added attachment: {name} -> {url}")

    def _extract_content(self, soup: BeautifulSoup, notice: Notice):
        """Extract main content text"""
        content_div = None

        # 1. Try Specific Selectors
        for selector in self.content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                break

        # 2. Fallback: Try to find content after the file list or title
        if not content_div:
            file_box = soup.select_one(".b-file-box, .b-file-list, .view-file")
            if not file_box:
                file_link = soup.select_one("a.b-file-dwn")
                if file_link:
                    file_box = file_link.find_parent("div") or file_link.find_parent(
                        "ul"
                    )

            if file_box:
                siblings = file_box.find_next_siblings()
                if siblings:
                    content_div = soup.new_tag("div")
                    for sib in siblings:
                        content_div.append(sib)

        # 3. Last resort: Main view container
        if not content_div:
            main_view = soup.select_one(".b-view, .view, .board-view")
            if main_view:
                content_div = main_view
                for exclude in main_view.select(
                    ".b-title-box, .b-info-box, .b-file-box, .view-title, .view-info, .view-file"
                ):
                    exclude.decompose()

        if not content_div:
            body = soup.body
            snippet = body.decode_contents()[:500] if body else "No Body"
<<<<<<< Updated upstream
=======

>>>>>>> Stashed changes
            logger.warning(
                f"[PARSER] Content not found for {notice.url}\n"
                f"Tried selectors: {', '.join(self.content_selectors)}\n"
                f"HTML snippet: {snippet}"
            )
        else:
            notice.content = content_div.get_text(separator=" ", strip=True)
            # Store content_div for image extraction if needed (though we pass soup to _extract_images)
            # Actually _extract_images logic uses content_div if available, but here we split methods.
            # We can re-find content_div or pass it.
            # For simplicity, let's keep _extract_images independent or pass content_div.
            # But to match signature, I'll let _extract_images find it again or just look globally.
            # Better: Make _extract_content return content_div? No, it modifies notice.
            # I'll let _extract_images find it again or look globally.

    def _extract_images(self, soup: BeautifulSoup, notice: Notice):
        """Extract images from soup"""
        images = []

        # Try to find content div again to prioritize images inside it
        content_div = None
        for selector in self.content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                break

        if content_div:
            images = content_div.find_all("img")

        if not images:
            images = soup.select('img[src*="_attach/yu/editor-image"]')

        if not images:
            images = soup.select(
                ".b-view-content img, .view-con img, .board-view-con img"
            )

        for img in images:
            src = img.get("src")
            if not src:
                continue

            full_url = urllib.parse.urljoin(notice.url, src)

            if full_url in notice.image_urls:
                continue

            # Filter small images
            width = img.get("width", "")
            height = img.get("height", "")
            try:
                if (
                    width
                    and width.replace("px", "").isdigit()
                    and int(width.replace("px", "")) < 50
                ):
                    continue
                if (
                    height
                    and height.replace("px", "").isdigit()
                    and int(height.replace("px", "")) < 50
                ):
                    continue
            except ValueError:
                pass

            skip_patterns = ["/icon/", "/emoji/", "/spacer.", "/blank."]
            if any(pattern in full_url.lower() for pattern in skip_patterns):
                continue

            notice.image_urls.append(full_url)
            logger.info(f"[PARSER] Added image: {full_url}")

    # Public helper methods for testing (proxies to private methods or implementation)
    def extract_text(self, soup: BeautifulSoup) -> str:
        # Create a dummy notice to capture content
        notice = Notice(
            site_key="test", article_id="test", title="test", url="http://test.com"
        )
        self._extract_content(soup, notice)
        return notice.content

    def extract_attachments(
        self, soup: BeautifulSoup, base_url: str
    ) -> List[Attachment]:
        notice = Notice(site_key="test", article_id="test", title="test", url=base_url)
        self._extract_attachments(soup, notice)
        return notice.attachments

    def extract_date(self, text: str) -> Optional[str]:
        # Helper for testing date extraction logic
        import re

        # Support YYYY-MM-DD, YYYY.MM.DD, and YYYY년 MM월 DD일
        match = re.search(r"(\d{4})[-.년]\s*(\d{1,2})[-.월]\s*(\d{1,2})", text)
        if match:
            return (
                f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
            )
        return None

    def extract_images(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        notice = Notice(site_key="test", article_id="test", title="test", url=base_url)
        self._extract_images(soup, notice)
        return notice.image_urls

    def clean_whitespace(self, text: str) -> str:
        return " ".join(text.split())
