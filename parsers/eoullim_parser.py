from typing import List
from bs4 import BeautifulSoup
import urllib.parse
from models.notice import Notice
from parsers.html_parser import HTMLParser
from core.logger import get_logger

logger = get_logger(__name__)

class EoullimParser(HTMLParser):
    """
    Parser for YU Eoullim system.
    Handles specific list layouts and status extraction.
    """

    def parse_list(self, html: str, site_key: str, base_url: str) -> List[Notice]:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        
        # Determine layout based on site_key or selector presence
        is_table_layout = "eoullim_external" in site_key
        
        # Try multiple selectors to capture both Notices (ul > li) and Regular items (div > div)
        rows = []
        
        # 1. Main configured selector (Regular items)
        rows.extend(soup.select(self.list_selector))
        
        # 2. Explicit Notice selector (often ul.program_list li with .notice class)
        # Avoid duplicates if list_selector already covers it
        notice_rows = soup.select("ul.program_list li")
        if notice_rows:
            # Only add if not already in rows (based on object identity)
            existing_ids = {id(r) for r in rows}
            for nr in notice_rows:
                if id(nr) not in existing_ids:
                     # Check if it looks like a notice or valid item
                     if nr.select_one(".notice_tag") or nr.select_one(".pro_btn.notice") or "공지" in nr.get_text():
                         rows.append(nr)

        if not rows:
            # Fallback/Retry logic or just log warning
            logger.warning(f"[EOULLIM] No items found with selector '{self.list_selector}' for {site_key}")
            return []

        for row in rows:
            try:
                title = ""
                link = ""
                status = ""
                article_id = ""

                if is_table_layout:
                    # Table Layout (program04)
                    # Selector: table.board_list tbody tr
                    # Title: td.subject a
                    # Link: td.subject a['href']
                    # Status: Often in the subject or separate column? 
                    # Browser analysis said: "Status: Often indicated by a label like span.notice within the first td or the td.subject."
                    
                    title_el = row.select_one(self.title_selector)
                    if not title_el:
                        continue
                    
                    title = title_el.get_text(strip=True)
                    link = title_el.get("href")
                    
                    # Extract Status from badges if present
                    status_el = row.select_one(".notice, .ing, .end") # Common classes
                    if status_el:
                        status = status_el.get_text(strip=True)

                else:
                    # Grid Layout (program01, 06)
                    # Selector: ul.program_list li
                    # Title: p.title a
                    # Link: p.title a['href']
                    # Status: div.pro_btn (Text: 상세보기/마감/신청중 etc.)
                    
                    title_el = row.select_one(self.title_selector)
                    if not title_el:
                        continue
                    
                    title = title_el.get_text(strip=True)
                    
                    # Extract Link
                    link_el = row.select_one(self.link_selector) if self.link_selector else title_el
                    link = link_el.get("href") if link_el else None
                    if not link and title_el.name == 'a':
                         link = title_el.get("href")

                    # Status logic
                    btn_div = row.select_one("div.pro_btn")
                    if btn_div:
                        status_text = btn_div.get_text(strip=True)
                        if "마감" in status_text:
                            status = "[마감]"
                        elif "신청중" in status_text or "접수중" in status_text or "진행중" in status_text:
                            status = "[신청중]"
                        elif "대기" in status_text:
                            status = "[대기]"
                        elif "공지" in status_text:
                             status = "[공지]"
                
                # Combine Status into Title if present
                if status and status not in title:
                    title = f"{status} {title}"

                # Link Processing
                if not link:
                    continue
                    
                full_url = urllib.parse.urljoin(base_url, link)
                
                # Article ID Parsing
                parsed = urllib.parse.urlparse(full_url)
                qs = urllib.parse.parse_qs(parsed.query)
                
                if "P_IDX" in qs:
                    article_id = qs["P_IDX"][0]
                elif "seq" in qs:
                    article_id = qs["seq"][0]
                elif "bb_code" in qs:
                    article_id = qs["bb_code"][0]
                
                if not article_id:
                    logger.warning(f"[EOULLIM] Could not extract ID from {full_url}")
                    continue

                items.append(Notice(
                    site_key=site_key,
                    article_id=article_id,
                    title=title,
                    url=full_url
                ))
                
            except Exception as e:
                logger.warning(f"[EOULLIM] Failed to parse item: {e}")
                continue
                
        logger.info(f"[EOULLIM] Found {len(items)} items for {site_key}")
        return items

    def _extract_attachments(self, soup: BeautifulSoup, notice: Notice):
        """Override to handle Eoullim specific file list structure"""
        # 1. Eoullim specific: ul.file_list
        file_list = soup.select("ul.file_list li a")
        for link in file_list:
            href = link.get("href")
            if not href:
                continue
                
            name = link.get_text(strip=True)
            if not name:
                name = "첨부파일"
                
            full_url = urllib.parse.urljoin(notice.url, href)
            
            # Use Attachment model
            from models.notice import Attachment
            notice.attachments.append(Attachment(name=name, url=full_url))
            logger.info(f"[EOULLIM] Added attachment: {name} -> {full_url}")

        # 2. Eoullim specific: dl style (program04)
        # Structure: dl > dt(첨부파일) + dl > dd > a
        # Or sometimes just .opp file_list
        # Browser analysis: "div.dk_view .opp" and "dl tag"
        
        # Let's try finding the "첨부파일" label and getting links from next sibling
        # Or just select all links inside .opp (option area)
        opp_files = soup.select(".opp dl dd a")
        for link in opp_files:
            href = link.get("href")
            if not href: continue
             
            name = link.get_text(strip=True)
            full_url = urllib.parse.urljoin(notice.url, href)
            
            from models.notice import Attachment
            # Check for duplicates
            if not any(a.url == full_url for a in notice.attachments):
                notice.attachments.append(Attachment(name=name, url=full_url))
                logger.info(f"[EOULLIM] Added attachment (dl): {name} -> {full_url}")

        # 2. Call parent for any other standard patterns (optional, but good for safety)
        super()._extract_attachments(soup, notice)
