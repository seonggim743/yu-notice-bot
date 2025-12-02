import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from services.scraper_service import ScraperService


async def verify_live_url():
    url = "https://www.yu.ac.kr/main/intro/yu-news.do?mode=view&articleNo=227576216&article.offset=10&articleLimit=10"

    print(f"Verifying URL: {url}")

    # Initialize ScraperService
    scraper = ScraperService(no_ai_mode=True)

    # Mock AI Service
    scraper.ai.analyze_notice = AsyncMock(
        return_value={"category": "일반", "summary": "Test Summary"}
    )

    # Mock Telegram to avoid spam (we only care about Discord)
    scraper.notifier.send_telegram = AsyncMock()

    # Patch aiohttp.ClientSession.post to capture Discord requests
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "12345"})
        mock_post.return_value.__aenter__.return_value = mock_response

        # Run test
        await scraper.run_test(url)

        # Inspect calls
        print("\n--- Inspecting POST calls ---")
        found_discord = False
        for call in mock_post.call_args_list:
            args, kwargs = call
            url_arg = args[0]

            if "discord.com" in url_arg:
                found_discord = True
                print(f"\n[Discord Call] URL: {url_arg}")

                payload = None
                if "json" in kwargs:
                    payload = kwargs["json"]
                elif "data" in kwargs:
                    # FormData
                    print("Payload is FormData (likely contains files)")
                    # We can't easily inspect FormData object structure here without digging deep,
                    # but we can check if 'payload_json' field exists if we really need to.
                    # For now, let's see if we can find the JSON payload.
                    data = kwargs["data"]
                    if hasattr(data, "_fields"):
                        for field in data._fields:
                            name, value = field[0], field[2]
                            if name == "payload_json":
                                import json

                                payload = json.loads(value)
                                break

                if payload:
                    if "embeds" in payload:
                        embeds = payload["embeds"]
                    elif "message" in payload and "embeds" in payload["message"]:
                        embeds = payload["message"]["embeds"]
                    else:
                        embeds = []

                    for embed in embeds:
                        print("Embed Fields:")
                        for field in embed.get("fields", []):
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
                            print(f" - {safe_name}: {safe_value}")

                            if "첨부파일" in field["name"]:
                                print("   -> FOUND ATTACHMENT FIELD")
                                # Check if filenames are decoded
                                if "%" not in field["value"]:
                                    print(
                                        "   -> ✅ Filenames appear decoded (no % signs)"
                                    )
                                else:
                                    print(
                                        "   -> ❌ Filenames might still be encoded (found % signs)"
                                    )

        if not found_discord:
            print("No Discord calls found.")


if __name__ == "__main__":
    asyncio.run(verify_live_url())
