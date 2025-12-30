
import asyncio
from repositories.notice_repo import NoticeRepository
from models.notice import Notice
from datetime import datetime

async def verify_fix():
    repo = NoticeRepository()
    
    # Create a dummy notice with empty arrays
    notice = Notice(
        site_key="test_site",
        article_id="test_id_999",
        title="Test Notice for Array Fix",
        url="http://example.com",
        content="Testing empty arrays",
        published_at=datetime.now(),
        author="Tester",
        # Empty arrays are the key here
        image_urls=[], 
        tags=[],
        target_grades=[],
        eligibility=[]
    )
    
    print("Attempting to upsert notice with empty arrays...")
    try:
        result = repo.upsert_notice(notice)
        if result:
            print("[SUCCESS] Notice upserted without error.")
            print(f"UUID: {result}")
        else:
            print("[FAILURE] Upsert returned None. Check logs.")
    except Exception as e:
        print(f"[ERROR] Exception during upsert: {e}")

if __name__ == "__main__":
    # NoticeRepository is sync but let's wrap just in case, though it seems sync in code
    # repo.upsert_notice is sync in the file I read.
    pass

    repo = NoticeRepository()
    
    # Create a dummy notice with empty arrays
    notice = Notice(
        site_key="test_site",
        article_id="test_id_999",
        title="Test Notice for Array Fix",
        url="http://example.com",
        content="Testing empty arrays",
        published_at=datetime.now(),
        author="Tester",
        image_urls=[], 
        tags=[],
        target_grades=[],
        eligibility=[]
    )
    
    print("Attempting to upsert notice with empty arrays...")
    result = repo.upsert_notice(notice)
    
    if result:
        print("[SUCCESS] Notice upserted without error.")
        print(f"UUID: {result}")
    else:
        print("[FAILURE] Upsert failed (returned None). This likely means the SQL fix is NOT applied yet.")

