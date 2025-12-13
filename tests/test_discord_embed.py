import asyncio
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

# Add project root to path
sys.path.append(os.getcwd())

from models.notice import Notice, Attachment


async def test_discord_embed():
    print("=" * 60)
    print("Discord Embed Verification")
    print("=" * 60)

    # Mock settings for both telegram and discord modules
    with patch("services.notification.discord.settings") as mock_discord_settings, \
         patch("services.notification.telegram.settings") as mock_telegram_settings:
        # Configure discord settings
        mock_discord_settings.DISCORD_BOT_TOKEN = "test_token"
        mock_discord_settings.DISCORD_CHANNEL_MAP = {"test_site": "123456789"}
        mock_discord_settings.USER_AGENT = "test_agent"
        
        # Configure telegram settings (for NotificationService init)
        mock_telegram_settings.TELEGRAM_TOKEN = None
        mock_telegram_settings.TELEGRAM_CHAT_ID = None

        from services.notification_service import NotificationService

        service = NotificationService()


        # Create dummy notice with attachments
        notice = Notice(
            site_key="test_site",
            article_id="1",
            title="Test Notice",
            url="http://example.com/notice/1",
            category="일반",
            published_at=datetime.now(),
            attachments=[
                Attachment(name="test_file.pdf", url="http://example.com/file1.pdf"),
                Attachment(
                    name="%EB%B6%99%EC%9E%84%ED%8C%8C%EC%9D%BC.hwp",
                    url="http://example.com/file2.hwp",
                ),  # 붙임파일.hwp
            ],
            summary="This is a summary.",
        )

        # Mock session
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "msg_123"})
        mock_response.read = AsyncMock(return_value=b"")

        mock_session.post.return_value.__aenter__.return_value = mock_response
        mock_session.get.return_value.__aenter__.return_value.status = (
            404  # Fail downloads
        )

        # Run send_discord
        print("Sending Discord notification...")
        await service.send_discord(mock_session, notice, is_new=True)

        # Inspect calls to session.post
        found_embed = False
        for call in mock_session.post.call_args_list:
            args, kwargs = call

            payload = None
            if "json" in kwargs:
                payload = kwargs["json"]
            elif "data" in kwargs:
                # If it's FormData, we might need to inspect it differently
                # But since downloads failed, it should use json or simple FormData
                # For this test, we assume it might be in 'payload_json' field of FormData if files are present
                # But here files are NOT present because downloads failed.
                pass

            if payload:
                # Check for thread creation payload
                if "message" in payload:
                    embeds = payload["message"].get("embeds", [])
                else:
                    embeds = payload.get("embeds", [])

                for embed in embeds:
                    print("\nChecking Embed Fields:")
                    for field in embed.get("fields", []):
                        # Use ascii encoding to avoid console errors
                        safe_name = (
                            field["name"]
                            .encode("ascii", "backslashreplace")
                            .decode("ascii")
                        )
                        safe_value = (
                            field["value"]
                            .encode("ascii", "backslashreplace")
                            .decode("ascii")
                        )
                        print(f"- Name: {safe_name}")
                        print(f"  Value: {safe_value}")

                        if field["name"] == "📎 첨부파일":
                            found_embed = True
                            print("  -> FOUND Attachment Field!")

                            # Check content
                            if (
                                "[test_file.pdf](http://example.com/file1.pdf)"
                                in field["value"]
                            ):
                                print("  -> Verified Link 1")
                            else:
                                print("  -> FAILED Link 1")

                            if (
                                "[붙임파일.hwp](http://example.com/file2.hwp)"
                                in field["value"]
                            ):
                                print("  -> Verified Link 2 (Decoded)")
                            else:
                                print("  -> FAILED Link 2 (Decoded)")

        if found_embed:
            print("\nSUCCESS: Attachment field verified.")
        else:
            print("\nFAILURE: Attachment field not found.")


if __name__ == "__main__":
    asyncio.run(test_discord_embed())
