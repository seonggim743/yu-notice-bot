
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

sys.path.append(os.getcwd())

# Mock settings before importing service
from types import SimpleNamespace

# Create concrete settings object
mock_settings = SimpleNamespace()
mock_settings.DISCORD_BOT_TOKEN = "test_token"
mock_settings.DISCORD_CHANNEL_MAP = {"yu_news": "123"}
mock_settings.LOG_LEVEL = "INFO"
mock_settings.LOG_MAX_BYTES = 1024 * 1024
mock_settings.LOG_BACKUP_COUNT = 3
mock_settings.LOG_FORMAT = "text"
mock_settings.LOG_DIR = "logs"
mock_settings.LOG_FILE = "bot.log"
mock_settings.TELEGRAM_TOKEN = "test_tel_token" # notification_service checks this
mock_settings.TELEGRAM_CHAT_ID = "1234"
mock_settings.TELEGRAM_TOPIC_MAP = {}
mock_settings.USER_AGENT = "test_agent"
mock_settings.DISCORD_FILE_SIZE_LIMIT = 8388608
mock_settings.DISCORD_ERROR_CHANNEL_ID = None
mock_settings.GEMINI_API_KEY = "test_key"
mock_settings.GEMINI_MODEL = "gemini-test"
mock_settings.SUPABASE_URL = "https://example.com"
mock_settings.SUPABASE_KEY = "test_sup_key"

# Mock core.config module
config_module = MagicMock()
config_module.settings = mock_settings
sys.modules["core.config"] = config_module

from models.notice import Notice
from services.notification_service import NotificationService

async def debug_discord_update():
    print("=" * 60)
    print("Discord Update Reply Debug")
    print("=" * 60)

    service = NotificationService()
    
    # Mock data
    notice = Notice(
        site_key="yu_news",
        article_id="1",
        title="Test Notice",
        url="http://example.com",
        summary="Updated summary content.",
        change_details={
            "title": "'Old Title' -> 'New Title'",
            "content": "Content changed."
        }
    )
    
    changes = notice.change_details
    modified_reason = "제목 변경, 내용 변경"
    
    # Mock session
    session = MagicMock()
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"id": "msg_999"})
    session.post.return_value.__aenter__.return_value = response
    
    # Call internal method directly to test the specific block
    # We pass existing_thread_id to trigger the update logic
    print("Invoking _send_discord_common with existing_thread_id...")
    await service._send_discord_common(
        session, 
        notice, 
        is_new=False, 
        modified_reason=modified_reason,
        thread_url="http://thread",
        message_url="http://message",
        headers={},
        existing_thread_id="thread_123",
        changes=changes
    )
    
    # Inspect calls
    found_field = False
    for call in session.post.call_args_list:
        args, kwargs = call
        if "json" in kwargs:
            payload = kwargs["json"]
            if "embeds" in payload:
                embed = payload["embeds"][0]
                sys.stdout.reconfigure(encoding='utf-8')
                print("\n[Update Embed]")
                print(f"Title: {embed.get('title')}")
                print(f"Description: {embed.get('description')}")
                print("\n[Fields]")
                for f in embed.get("fields", []):
                    print(f"- {f['name']}: {f['value']}")
                    if f['name'] == "🔄 변경 요약":
                        found_field = True

    if found_field:
        print("\nSUCCESS: 'Change Summary' field found in update payload.")
    else:
        print("\nFAILURE: 'Change Summary' field NOT found.")

if __name__ == "__main__":
    asyncio.run(debug_discord_update())
