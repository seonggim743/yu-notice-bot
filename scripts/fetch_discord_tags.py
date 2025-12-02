import asyncio
import aiohttp
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


async def fetch_tags():
    bot_token = settings.DISCORD_BOT_TOKEN
    channel_map = settings.DISCORD_CHANNEL_MAP

    if not bot_token:
        print("❌ DISCORD_BOT_TOKEN is missing")
        return

    headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        for site_key, channel_id in channel_map.items():
            print(
                f"\n[INFO] Fetching tags for {site_key} (Channel ID: {channel_id})..."
            )
            url = f"https://discord.com/api/v10/channels/{channel_id}"

            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    channel_type = data.get("type")
                    channel_name = data.get("name")
                    print(
                        f"[INFO] Channel: {channel_name} | Type: {channel_type} (15=Forum, 0=Text)"
                    )

                    tags = data.get("available_tags", [])
                    if tags:
                        print(f"[SUCCESS] Found {len(tags)} tags:")
                        for tag in tags:
                            print(
                                f"  - ID: {tag['id']} | Name: {tag['name']} | Emoji: {tag.get('emoji_name', 'None')}"
                            )
                    else:
                        print("[WARN] No tags found (or not a Forum Channel?)")
                else:
                    print(
                        f"[ERROR] Failed to fetch channel info: {resp.status} {await resp.text()}"
                    )


if __name__ == "__main__":
    # Force UTF-8 for Windows console if possible, but removing emojis is safer
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(fetch_tags())
