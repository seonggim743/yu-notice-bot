
import sys
import os
from datetime import datetime
from pprint import pprint

# Add project root to path
sys.path.append(os.getcwd())

from models.notice import Notice
from services.notification.formatters import create_discord_embed

def debug_discord_format():
    print("=" * 60)
    print("Discord Embed Debug")
    print("=" * 60)

    # 1. Mock Notice
    notice = Notice(
        site_key="yu_news",
        article_id="12345",
        title="[마감][RISE-MEGA] 「MDX마스터클래스 - HD현대로보틱스-자동화설비 운영을 위한 로봇(Hi6) 교육」참가학생 모집",
        url="https://example.com/notice/12345",
        category="학사",
        published_at=datetime.now(),
        summary="이것은 요약입니다.",
    )

    # 2. Mock Changes (Simulating what scraper_service produces)
    changes = {
        "title": "'[RISE-MEGA] ...' -> '[마감][RISE-MEGA] ...'",
        "content": "내용이 변경되었습니다.",
        "image": "이미지 변경됨"
    }

    # 3. Create Embed (Existing Code)
    # Using modified_reason matching the user's example
    modified_reason = "제목 변경, 내용 변경"
    
    embed = create_discord_embed(notice, is_new=False, modified_reason=modified_reason, changes=changes)

    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("\n[Generated Embed]")
    print(f"Title: {embed.get('title')}")
    print(f"Description:\n---\n{embed.get('description')}\n---")
    
    print("\n[Fields]")
    for f in embed.get("fields", []):
        print(f"- {f['name']}: {f['value']}")

if __name__ == "__main__":
    debug_discord_format()
