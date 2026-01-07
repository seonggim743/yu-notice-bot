
import aiohttp
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

class DevNotifier:
    """
    Sends system alerts and error reports to a dedicated Dev channel.
    """
    def __init__(self):
        self.platform = settings.DEV_PLATFORM.lower()
        self.telegram_token = settings.TELEGRAM_TOKEN
        
        # Resolve Channel/Topic ID from Maps
        if self.platform == "telegram":
            # For Telegram, we send to the main Chat ID but use the 'dev' topic if available
            self.channel_id = settings.TELEGRAM_CHAT_ID # Main Chat ID
            self.topic_id = settings.TELEGRAM_TOPIC_MAP.get("dev") # Optional Topic ID
        elif self.platform == "discord":
            # For Discord, map gives the Channel ID or Webhook
            self.channel_id = settings.DISCORD_CHANNEL_MAP.get("dev")
            self.topic_id = None
        else:
            self.channel_id = None
            self.topic_id = None

    async def send_alert(self, message: str):
        """
        Sends an alert message to the configured Dev channel.
        """
        if not self.channel_id:
            return

        try:
            async with aiohttp.ClientSession() as session:
                if self.platform == "telegram":
                    await self._send_telegram(session, message)
                elif self.platform == "discord":
                    await self._send_discord(session, message)
                else:
                    logger.warning(f"[DEV] Unknown dev platform: {self.platform}")

        except Exception as e:
            logger.error(f"[DEV] Failed to send alert: {e}")

    async def _send_telegram(self, session: aiohttp.ClientSession, text: str):
        if not self.telegram_token or not self.channel_id:
            return
            
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": f"🚨 <b>SYSTEM ALERT</b>\n\n{text}",
            "parse_mode": "HTML"
        }
        if self.topic_id:
            payload["message_thread_id"] = self.topic_id
        
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                logger.error(f"[DEV] Telegram alert failed: {await resp.text()}")
            else:
                logger.info("[DEV] Alert sent to Telegram")

    async def _send_discord(self, session: aiohttp.ClientSession, text: str):
        if not self.channel_id:
            return

        # Assuming Bot Token based send to channel
        url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
        headers = {
            "Authorization": f"Bot {settings.DISCORD_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {"content": f"🚨 **SYSTEM ALERT**\n\n{text}"}

        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in [200, 201]:
                logger.error(f"[DEV] Discord alert failed: {await resp.text()}")
            else:
                logger.info("[DEV] Alert sent to Discord")
