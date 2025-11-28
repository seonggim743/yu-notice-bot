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
    def __init__(self, list_selector: str, title_selector: str, link_selector: str, content_selector: str):
        self.list_selector = list_selector
        self.title_selector = title_selector
        self.link_selector = link_selector
        # Expanded selectors for YU sites
        self.content_selectors = [
            content_selector, 
            ".b-content-box", 
            ".view-con", 
            ".board-view-con", 
            "#article_text", 
            ".bbs_view", 
            ".view_content",
            "div[class*='content']",
            "div[class*='view']"
        ]

    def parse_list(self, html: str, site_key: str, base_url: str) -> List[Notice]:
        soup = BeautifulSoup(html, 'html.parser')
        items = []
        rows = soup.select(self.list_selector)
        
        if not rows:
            logger.warning(f"[PARSER] No items found with selector '{self.list_selector}' for {site_key}")

        for row in rows:
            try:
                title_el = row.select_one(self.title_selector)
                if not title_el: continue
                
                title = title_el.get_text(strip=True)
                
                # Link
                link_el = row.select_one(self.link_selector)
                href = link_el.get('href') if link_el else title_el.get('href')
                
                if not href:
                    continue
                
                # Skip obviously invalid links before URL joining
                if href.startswith(('tel:', 'mailto:', 'javascript:')):
                    logger.debug(f"[PARSER] Skipping invalid scheme: {href}")
                    continue
                
                full_url = urllib.parse.urljoin(base_url, href)
                parsed = urllib.parse.urlparse(full_url)
                
                # Skip hash-only links
                if parsed.fragment and not parsed.path:
                    logger.debug(f"[PARSER] Skipping hash-only link: {full_url}")
                    continue
                
                # Skip resource download links (images, PDFs, etc. without article ID)
                if any(x in parsed.path.lower() for x in ['resourcedown', 'filedown', 'download.do']):
                    if 'articleno' not in parsed.query.lower() and 'seq' not in parsed.query.lower():
                        logger.debug(f"[PARSER] Skipping resource download link: {full_url}")
                        continue
                
                # Article ID Extraction (Generic approach: query param or path)
                qs = urllib.parse.parse_qs(parsed.query)
                
                # Common patterns for ID
                article_id = None
                if 'articleNo' in qs: article_id = qs['articleNo'][0]
                elif 'seq' in qs: article_id = qs['seq'][0]
                elif 'id' in qs: article_id = qs['id'][0]
                
                if not article_id:
                    # Only warn if it looks like a notice link (has .do extension or board in path)
                    if '.do' in parsed.path or 'board' in parsed.path.lower() or 'notice' in parsed.path.lower():
                        logger.warning(f"[PARSER] Could not extract article ID from URL: {full_url}")
                    continue

                items.append(Notice(
                    site_key=site_key,
                    article_id=article_id,
                    title=title,
                    url=full_url
                ))
            except Exception as e:
                logger.warning(f"[PARSER] Failed to parse list item: {e}")
                continue
        
        logger.info(f"[PARSER] Found {len(items)} items for {site_key}")
        return items

    def parse_detail(self, html: str, notice: Notice) -> Notice:
        soup = BeautifulSoup(html, 'html.parser')
        
        content_div = None
        for selector in self.content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                logger.debug(f"[PARSER] Content found with selector: {selector}")
                break
        
        if not content_div:
            # Log why we failed
            body = soup.body
            snippet = body.decode_contents()[:500] if body else "No Body"
            logger.error(
                f"[PARSER] Content not found for {notice.url}\n"
                f"Tried selectors: {', '.join(self.content_selectors)}\n"
                f"HTML snippet: {snippet}"
            )
        else:
            notice.content = content_div.get_text(separator=' ', strip=True)
            
            # Attachments
            # Generic file download link detection
            files = content_div.find_all('a', href=True)
            for f in files:
                href = f['href']
                if 'file' in href.lower() or 'download' in href.lower() or 'attach' in href.lower():
                    name = f.get_text(strip=True) or href.split('/')[-1]
                    url = urllib.parse.urljoin(notice.url, href)
                    notice.attachments.append(Attachment(name=name, url=url))
            
            # Image
            img = content_div.find('img')
            if img and img.get('src'):
                notice.image_url = urllib.parse.urljoin(notice.url, img['src'])
                
        return notice
