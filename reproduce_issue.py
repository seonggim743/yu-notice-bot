import asyncio
import aiohttp
import logging
from parsers.html_parser import HTMLParser
from models.notice import Notice
from core.config import settings

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_attachment_extraction():
    """Test 1: Verify attachment extraction from HTML"""
    logger.info("=" * 60)
    logger.info("TEST 1: Attachment Extraction")
    logger.info("=" * 60)
    
    test_url = "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227589805&article.offset=0&articleLimit=10"
    
    parser = HTMLParser("table tbody tr", "a", "a", ".b-view-content")
    
    headers = {
        'User-Agent': settings.USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(test_url, headers=headers) as resp:
            html = await resp.text()
    
    # Create dummy notice
    notice = Notice(
        site_key="yu_news",
        article_id="227589805",
        title="Test Notice",
        url=test_url
    )
    
    # Parse detail to extract attachments
    notice = parser.parse_detail(html, notice)
    
    logger.info(f"✓ Content length: {len(notice.content)} characters")
    logger.info(f"✓ Attachments found: {len(notice.attachments)}")
    
    for idx, att in enumerate(notice.attachments, 1):
        logger.info(f"  {idx}. Name: {att.name}")
        logger.info(f"     URL: {att.url}")
    
    if len(notice.attachments) == 2:
        logger.info("✅ PASS: Found expected 2 attachments")
        return True
    else:
        logger.error(f"❌ FAIL: Expected 2 attachments, found {len(notice.attachments)}")
        return False

async def test_file_download():
    """Test 2: Verify file download with proper headers"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: File Download")
    logger.info("=" * 60)
    
    test_files = [
        {
            "name": "[IGE Korea] 미국 인턴 모집_서연이화.pdf",
            "url": "https://www.yu.ac.kr/main/intro/yu-news.do?mode=fileDownload&articleNo=227589805&attachNo=361786"
        },
        {
            "name": "영문이력서 GuideTemplate (2).docx",
            "url": "https://www.yu.ac.kr/main/intro/yu-news.do?mode=fileDownload&articleNo=227589805&attachNo=361787"
        }
    ]
    
    referer = "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227589805&article.offset=0&articleLimit=10"
    
    headers = {
        'Referer': referer,
        'User-Agent': settings.USER_AGENT,
        'Accept': '*/*',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Connection': 'keep-alive'
    }
    
    all_passed = True
    
    async with aiohttp.ClientSession() as session:
        for idx, file_info in enumerate(test_files, 1):
            logger.info(f"\nDownloading file {idx}/{len(test_files)}: {file_info['name']}")
            
            try:
                async with session.get(file_info['url'], headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    logger.info(f"  Status: {resp.status}")
                    logger.info(f"  Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
                    
                    if 'Content-Disposition' in resp.headers:
                        logger.info(f"  Content-Disposition: {resp.headers['Content-Disposition']}")
                    
                    if resp.status == 200:
                        data = await resp.read()
                        size = len(data)
                        logger.info(f"  ✓ Downloaded: {size:,} bytes ({size / 1024:.2f} KB)")
                        
                        # Verify it's actually a file (not HTML error page)
                        if data.startswith(b'%PDF') or data.startswith(b'PK'):
                            logger.info(f"  ✓ File format verified (PDF or DOCX)")
                        else:
                            logger.warning(f"  ⚠ Unexpected file format (first 20 bytes): {data[:20]}")
                        
                        logger.info(f"✅ PASS: {file_info['name']}")
                    else:
                        logger.error(f"❌ FAIL: HTTP {resp.status}")
                        error_body = await resp.text()
                        logger.error(f"  Response: {error_body[:200]}")
                        all_passed = False
                        
            except Exception as e:
                logger.error(f"❌ FAIL: {e}")
                all_passed = False
    
    return all_passed

async def test_full_notification_flow():
    """Test 3: Full notification flow (requires .env setup)"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Full Notification Flow")
    logger.info("=" * 60)
    
    from services.scraper_service import ScraperService
    
    logger.info("Running full test with ScraperService.run_test()...")
    
    service = ScraperService()
    test_url = "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227589805&article.offset=0&articleLimit=10"
    
    try:
        await service.run_test(test_url)
        logger.info("✅ PASS: Full notification flow completed")
        return True
    except Exception as e:
        logger.error(f"❌ FAIL: {e}")
        return False

async def main():
    logger.info("\n" + "=" * 60)
    logger.info("YU NOTICE BOT - FILE DOWNLOAD TEST SUITE")
    logger.info("=" * 60)
    
    results = {}
    
    # Test 1: Attachment Extraction
    results['extraction'] = await test_attachment_extraction()
    
    # Test 2: File Download
    results['download'] = await test_file_download()
    
    # Test 3: Full Flow (optional, requires Telegram/Discord setup)
    if settings.TELEGRAM_TOKEN:
        results['full_flow'] = await test_full_notification_flow()
    else:
        logger.info("\n⚠ Skipping Test 3: TELEGRAM_TOKEN not configured")
        results['full_flow'] = None
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST SUMMARY")
    logger.info("=" * 60)
    
    for test_name, result in results.items():
        if result is None:
            status = "⊘ SKIPPED"
        elif result:
            status = "✅ PASSED"
        else:
            status = "❌ FAILED"
        logger.info(f"{status}: {test_name}")
    
    passed = sum(1 for r in results.values() if r is True)
    total = sum(1 for r in results.values() if r is not None)
    
    logger.info(f"\nResult: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed!")
    else:
        logger.warning("⚠ Some tests failed. Check logs above for details.")

if __name__ == "__main__":
    asyncio.run(main())
