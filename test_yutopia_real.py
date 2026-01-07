import asyncio
import os
from services.scraper_service import ScraperService
from core.logger import get_logger

# Setup logging
import logging
logging.basicConfig(level=logging.INFO)

async def main():
    service = ScraperService(no_ai_mode=True)
    # Target URL from our analysis
    test_url = "https://yutopia.yu.ac.kr/ko/program/all/view/20624?sort=date" 
    

    
    print("--- Testing List Parsing ---")
    service.filter_targets("yutopia")

    

    from services.scraper.fetcher import NoticeFetcher
    from parsers.yutopia_parser import YutopiaParser
    
    fetcher = NoticeFetcher()
    session = await fetcher.create_session()
    
    target = [t for t in service.targets if t["key"] == "yutopia"][0]
    parser = target["parser"] # This should be YutopiaParser instance
    
    print(f"Parser Type: {type(parser)}")
    
    # 1. Test List
    print(f"\nFetching List: {target['url']}")
    html = await fetcher.fetch_url(session, target["url"])
    items = parser.parse_list(html, "yutopia", target["base_url"])
    print(f"Found {len(items)} items.")
    if items:
        item = items[0]
        print(f"First Item: {item.title} ({item.url})")
        print(f"Extra Info(List): {item.extra_info}")
        
        # 2. Test Detail
        print(f"\nFetching Detail: {item.url}")
        detail_html = await fetcher.fetch_url(session, item.url)
        # Note: We already saved debug html, skipping save
        item = parser.parse_detail(detail_html, item)
        
        print(f"Content Length: {len(item.content)}")
        print(f"Eligibility: {item.eligibility}")
        print(f"Dept: {item.target_dept}")
        print(f"Op Dates: {item.start_date} ~ {item.end_date}")
        print(f"Target Grade/Gender: {item.extra_info.get('target_grade_gender')}")
        print(f"App Period: {item.extra_info.get('application_period')}")
        print(f"Capacity: {item.extra_info.get('capacity_info')}")
        print(f"Content Length: {len(item.content)}")
        print(f"Attachments: {[a.name for a in item.attachments]}")
        print(f"Images: {len(item.image_urls)}")
        print(f"Notice Tab Link Present: {'[공지사항 탭 바로가기]' in item.content}")

    await session.close()

if __name__ == "__main__":
    asyncio.run(main())
