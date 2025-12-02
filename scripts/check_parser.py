import asyncio
import sys
import os
import aiohttp

# Add project root to path
sys.path.append(os.getcwd())

from parsers.html_parser import HTMLParser
from models.notice import Notice


async def check_parser():
    url = "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227576216&article.offset=10&articleLimit=10"
    print(f"Checking URL: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            html = await resp.text()

    parser = HTMLParser("table tbody tr", "a", "a", ".b-view-content")

    # Create dummy item
    item = Notice(
        site_key="yu_news", article_id="test", title="Test", url=url, published_at=None
    )

    item = parser.parse_detail(html, item)

    print(f"\nAttachments Found: {len(item.attachments)}")

    async with aiohttp.ClientSession(headers=headers) as session:
        for att in item.attachments:
            safe_name = att.name.encode("ascii", "backslashreplace").decode("ascii")
            print(f"Name: {safe_name}")
            print(f"URL: {att.url}")

            if "%" in att.name:
                print("-> [WARN] Name appears to be URL-encoded!")
            else:
                print("-> [OK] Name appears decoded.")

            # Check Content-Disposition
            try:
                async with session.get(att.url) as file_resp:
                    cd = file_resp.headers.get("Content-Disposition", "None")
                    print(f"Content-Disposition: {cd}")
            except Exception as e:
                print(f"Failed to fetch file: {e}")


if __name__ == "__main__":
    asyncio.run(check_parser())
