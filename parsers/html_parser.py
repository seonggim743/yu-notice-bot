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
            ".b-content-box .fr-view",  # Froala editor content (most precise)
            ".b-content-box", 
            ".fr-view",                  # Froala editor (fallback)
            ".view-con", 
            ".board-view-con", 
            "#article_text", 
            ".bbs_view", 
            ".view_content",
            ".b-view-content"
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
                
                # Clean up title - remove common markers (N=New, HOT, UP, etc.)
                # These appear at the end of titles on YU notice boards
                title = re.sub(r'\s*[NUHOT]+\s*$', '', title)
                title = re.sub(r'\s*New\s*$', '', title, flags=re.IGNORECASE)
                title = title.strip()
                
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
        
        # IMPORTANT: Extract attachments FIRST before any content processing
        # that might remove .b-file-box elements!
        
        # === ATTACHMENTS EXTRACTION (MUST BE FIRST) ===
        files = []
        
        # 1. Try to find b-file-box container (most reliable for YU sites)
        file_box = soup.select_one('.b-file-box')
        if file_box:
            # Select only primary download links (a.b-file-dwn) to avoid duplicates
            # YU sites have both a.b-file-dwn (main link) and a.b-file-util (duplicate button)
            files = file_box.select('a.b-file-dwn')
            logger.info(f"[PARSER] Found {len(files)} attachments in .b-file-box")
        
        # 2. Fallback: Try other common file list containers
        if not files:
            file_containers = soup.select('.b-file-list, .view-file, .file-list')
            for container in file_containers:
                files.extend(container.select('a'))
            if files:
                logger.info(f"[PARSER] Found {len(files)} attachments in fallback containers")
        
        # 3. Last resort: Find all links with file-related hrefs
        if not files:
            debug_links = soup.find_all('a', href=True)
            files = [l for l in debug_links if 'fileDownload' in l.get('href', '') or 'attachNo' in l.get('href', '')]
            if files:
                logger.info(f"[PARSER] Using fallback file detection, found {len(files)} links")
        
        # Process found file links
        for f in files:
            href = f.get('href')
            if not href: 
                continue
            
            # Get filename from link text
            name = f.get_text(strip=True)
            
            # Clean up common Korean text artifacts
            if '다운로드' in name or '첨부파일' in name:
                # Try to find actual filename in nearby elements
                parent = f.parent
                if parent:
                    # Look for filename in parent text
                    parent_text = parent.get_text(strip=True)
                    # Remove download-related text
                    for remove_text in ['첨부파일 다운로드', '다운로드', '첨부파일']:
                        parent_text = parent_text.replace(remove_text, '')
                    # Clean up extra whitespace and download counts
                    parent_text = re.sub(r'\(다운로드\s*:\s*\d+\)', '', parent_text).strip()
                    if parent_text and len(parent_text) > 3:
                        name = parent_text.strip()
            
            # Fallback: extract from URL
            if not name or name == href or len(name) < 3:
                name = href.split('/')[-1]
                # Try to decode URL-encoded filename
                from urllib.parse import unquote
                name = unquote(name)
            
            # Build full URL
            url = urllib.parse.urljoin(notice.url, href)
            
            # Avoid duplicates (check both URL and name)
            if not any(a.url == url for a in notice.attachments):
                notice.attachments.append(Attachment(name=name, url=url))
                logger.info(f"[PARSER] Added attachment: {name} -> {url}")
        
        # === CONTENT EXTRACTION (AFTER ATTACHMENTS) ===
        
        # 1. Try Specific Selectors
        content_div = None
        for selector in self.content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                logger.debug(f"[PARSER] Content found with selector: {selector}")
                break
        
            # 2. Fallback: Try to find content after the file list or title
            if not content_div:
                # Common YU structure: Title -> Info -> Files -> Content
                # Try to find the file box
                file_box = soup.select_one('.b-file-box, .b-file-list, .view-file')
                
                # If no explicit file box, try to find the container of file links
                if not file_box:
                    file_link = soup.select_one('a.b-file-dwn')
                    if file_link:
                        file_box = file_link.find_parent('div') or file_link.find_parent('ul')
                
                if file_box:
                    # Get all siblings after the file box
                    siblings = file_box.find_next_siblings()
                    if siblings:
                        # Create a dummy div to hold the content
                        content_div = soup.new_tag('div')
                        for sib in siblings:
                            content_div.append(sib)
                        logger.info(f"[PARSER] Content extracted from siblings after file box")
            
            # If still no content, try looking for a generic 'view-con' or similar that might have been missed
            # or try to get text from the main wrapper excluding known non-content parts
            if not content_div:
                # Last resort: div.b-view-content might be missing, but maybe there's a div with style or just text?
                # Let's try to find the main container 'div.b-view' or 'div.view'
                main_view = soup.select_one('.b-view, .view, .board-view')
                if main_view:
                    content_div = main_view
                    # Remove title, info, files from this container if they exist inside
                    for exclude in main_view.select('.b-title-box, .b-info-box, .b-file-box, .view-title, .view-info, .view-file'):
                        exclude.decompose()
                    logger.info(f"[PARSER] Content extracted from main view container (cleaned)")

        if not content_div:
            # Log why we failed but don't stop processing attachments/images
            body = soup.body
            snippet = body.decode_contents()[:500] if body else "No Body"
            logger.warning(
                f"[PARSER] Content not found for {notice.url}\n"
                f"Tried selectors: {', '.join(self.content_selectors)}\n"
                f"HTML snippet: {snippet}"
            )
        else:
            notice.content = content_div.get_text(separator=' ', strip=True)
            
        
        # Image (Always try to find images)
        img = None
        # 1. Try finding inside content_div if it exists
        if content_div:
            img = content_div.find('img')
        
        # 2. If not found, look for YU Editor images globally (strong signal)
        if not img:
            img = soup.select_one('img[src*="_attach/yu/editor-image"]')
        
        # 3. If still not found, look for any large image in the view area
        if not img:
            # Try generic view classes again for images
            img = soup.select_one('.b-view-content img, .view-con img, .board-view-con img')

        if img and img.get('src'):
            notice.image_url = urllib.parse.urljoin(notice.url, img['src'])
                
        return notice
