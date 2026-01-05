from typing import List, Optional
from bs4 import BeautifulSoup
import re
import urllib.parse
from datetime import datetime
from models.notice import Notice, Attachment
from parsers.html_parser import BaseParser, HTMLParser
from core.logger import get_logger

logger = get_logger(__name__)

class YutopiaParser(BaseParser):
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
        self.content_selector = content_selector
        # Use HTMLParser's helper methods for common tasks if needed, 
        # or composition. We'll simply inherit common logic usage patterns.
        self.html_parser_helper = HTMLParser(
            list_selector, title_selector, link_selector, content_selector
        )

    def parse_list(self, html: str, site_key: str, base_url: str) -> List[Notice]:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # The list selector is likely 'ul.columns-4 > li' or similar based on analysis
        rows = soup.select(self.list_selector)
        
        if not rows:
            logger.warning(
                f"[YUTOPIA] No items found with selector '{self.list_selector}'"
            )
            return []

        for row in rows:
            try:
                # Find the main link (usually the whole card or title)
                link_el = row.select_one(self.link_selector)
                if not link_el:
                    # Fallback: try finding any 'a' tag
                    link_el = row.select_one("a")
                
                if not link_el:
                    continue

                href = link_el.get("href")
                if not href:
                    continue

                # Title extraction
                title_el = row.select_one(self.title_selector)
                if title_el:
                    title = title_el.get_text(strip=True)
                else:
                    # Fallback: use text inside the link or strict finding
                    # Analyzed: .title_wrap b.title
                    title_el_fallback = row.select_one("b.title")
                    title = title_el_fallback.get_text(strip=True) if title_el_fallback else link_el.get_text(strip=True)

                # Clean title
                title = re.sub(r"\s*[NUHOT]+\s*$", "", title)
                title = title.strip()

                full_url = urllib.parse.urljoin(base_url, href)
                
                # Extract ID from URL path: /ko/program/all/view/20624 -> 20624
                # Regex for /view/digits
                id_match = re.search(r"/view/(\d+)", full_url)
                if id_match:
                    article_id = id_match.group(1)
                else:
                    logger.warning(f"[YUTOPIA] Could not extract ID from URL: {full_url}")
                    continue

                notice = Notice(
                    site_key=site_key,
                    article_id=article_id,
                    title=title,
                    url=full_url,
                )

                # Extract Dates and Status from List Item
                # Analyzed: small.date_layer contains time elements
                # Status: label.CLOSED / label.OPEN
                
                status_label = row.select_one("label.state, label.CLOSED, label.OPEN")
                if status_label:
                    status_text = status_label.get_text(strip=True)
                    # We can prepend status to title or store in extra_info
                    if status_text in ["마감", "CLOSED"]:
                         notice.title = f"[마감] {notice.title}"
                    elif status_text in ["접수", "OPEN"]:
                         notice.title = f"[접수중] {notice.title}"

                # Date Parsing
                # Look for "신청:" or "운영:" text in small tags
                date_layers = row.select("small.date_layer")
                for layer in date_layers:
                    text = layer.get_text()
                    if "신청" in text:
                        # Parse application period
                        # Format often: 2024.01.01 ~ 2024.01.31
                        times = layer.select("time")
                        if len(times) >= 2:
                            # Start and End
                            start_str = times[0].get("datetime") or times[0].get_text(strip=True)
                            end_str = times[1].get("datetime") or times[1].get_text(strip=True)
                            # Store in extra_info or parse to specific fields if Notice model supports
                            notice.extra_info["application_start"] = start_str
                            notice.extra_info["application_end"] = end_str
                            
                            # Try to parse deadline
                            try:
                                # Simple parse if format is YYYY-MM-DD
                                notice.deadline = end_str.split(" ")[0]
                            except:
                                pass

                items.append(notice)

            except Exception as e:
                logger.warning(f"[YUTOPIA] Failed to parse list item: {e}")
                continue

        logger.info(f"[YUTOPIA] Found {len(items)} items")
        return items

    def parse_detail(self, html: str, notice: Notice) -> Notice:
        soup = BeautifulSoup(html, "html.parser")

        # 1. Content Extraction
        # Target: div.description div[data-role="wysiwyg-content"]
        content_div = soup.select_one(self.content_selector)
        if not content_div:
            # Fallback
            content_div = soup.select_one('.view-content, .description')
        
        if content_div:
            notice.content = content_div.get_text(separator="\n", strip=True)
            
            # 2. Image Extraction
            images = content_div.select("img")
            for img in images:
                src = img.get("src")
                if src:
                    full_img_url = urllib.parse.urljoin(notice.url, src)
                    if full_img_url not in notice.image_urls:
                         notice.image_urls.append(full_img_url)

        # 3. Attachments
        # Target: a[href*="/attachment/download/"]
        att_links = soup.select('a[href*="/attachment/download/"]')
        for link in att_links:
            href = link.get("href")
            name = link.get_text(strip=True)
            # Clean size prefix (e.g. "217.99KB붙임..." -> "붙임...")
            name = re.sub(r"^[\d\.]+[KMG]?B", "", name, flags=re.IGNORECASE).strip()
            if not name:
                name = "첨부파일"
            
            full_att_url = urllib.parse.urljoin(notice.url, href)
            # Ensure protocol is present
            if full_att_url.startswith("//"):
                full_att_url = "https:" + full_att_url
            
            # Check for duplicates
            if not any(a.url == full_att_url for a in notice.attachments):
                notice.attachments.append(Attachment(name=name, url=full_att_url))

        # 4. Add Notice Tab Link
        # URL pattern: /view/{ID}/notice
        # We append a helpful link to the content
        notice_tab_url = notice.url.replace("/view/", "/view/").rstrip("?") + "/notice"
        
        # Check if notice tab link exists in DOM to be sure
        notice_link = soup.select_one('a[href$="/notice"]')
        if notice_link:
             # Use real link if found
             notice_tab_url = urllib.parse.urljoin(notice.url, notice_link.get("href"))

        # Append to content
        notice.content += f"\n\n[공지사항 탭 바로가기]({notice_tab_url})"

        # 5. Extract Details (Target, Grade, Dept, etc.)
        # div.title > ul > li
        title_box = soup.select_one("div.title")
        if title_box:
            # Target
            target_el = title_box.select_one("li.target span")
            if target_el:
                notice.eligibility = [t.strip() for t in target_el.get_text(strip=True).split("/")]
            
            # Grade/Gender
            # Find li that contains label "학년/성별" (using simple iteration as CSS :has is not fully supported in old BS4)
            for li in title_box.select("li"):
                label = li.select_one("label")
                if label and "학년/성별" in label.get_text():
                    span = li.select_one("span")
                    if span:
                        notice.extra_info["target_grade_gender"] = span.get_text(strip=True)
                    break
            
            # Department
            dept_el = title_box.select_one("li.department span")
            if dept_el:
                notice.target_dept = dept_el.get_text(strip=True)

        # 6. Extract Periods (Operation & Application) from Form/Table
        # "운영기간": form[data-role=topic] li.tbody span.title p time
        # "신청기간": form[data-role=topic] li.tbody span.date
        # "신청현황": form[data-role=topic] li.tbody span.status p
        
        # 6. Extract Periods (Operation & Application) from Form/Table
        # We need to find the form that contains the detailed table (li.tbody)
        # There are multiple forms, we want the one in "상세일정 및 신청하기" section usually
        
        # Try finding the form with li.tbody
        topic_form = None
        for form in soup.select("form[data-role='topic']"):
            if form.select_one("li.tbody"):
                topic_form = form
                break
        
        if topic_form:
            tbody = topic_form.select_one("li.tbody")
            if tbody:
                # Operation Period
                title_span = tbody.select_one("span.title")
                if title_span:
                    times = title_span.select("time")
                    if len(times) >= 2:
                        start = times[0].get("datetime") or times[0].get_text(strip=True)
                        end = times[1].get("datetime") or times[1].get_text(strip=True)
                        notice.start_date = start
                        notice.end_date = end
                
                # Application Period (Detailed)
                date_span = tbody.select_one("span.date")
                if date_span:
                     # Often "YYYY.MM.DD ... 부터 ... 까지"
                     # Use get_text and clean up
                     text = date_span.get_text(" ", strip=True)
                     # Clean up logic if needed, e.g. remove "부터", "까지"
                     text = text.replace("부터", "").replace("까지", "").strip()
                     # Replace multiple spaces with " ~ " if it looks like two dates
                     text = re.sub(r'\s{2,}', ' ~ ', text)
                     notice.extra_info["application_period"] = text

                # Capacity / Status
                status_span = tbody.select_one("span.status")
                if status_span:
                    # Remove "접수마감" label if present to get just numbers?
                    # or get all text. "20 팀 / 무제한 ... 접수마감"
                    caps = []
                    for el in status_span.select("p"):
                         if "awaiter" not in el.get("class", []):
                              caps.append(el.get_text(strip=True))
                    
                    status_text = " ".join(caps).strip()
                    if status_text:
                        notice.extra_info["capacity_info"] = status_text
                    
                    # Also checking for closed status
                    if status_span.select_one(".closed"):
                         notice.extra_info["status_label"] = "접수마감"
                    elif status_span.select_one(".end"): # Check other status classes if any
                         pass

        return notice
