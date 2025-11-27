import os
import json
import logging
import aiohttp
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

async def send_telegram(session: aiohttp.ClientSession, message: str, topic_id: int = None,
                        buttons: List[Dict] = None, photo_data: bytes = None, reply_to_message_id: int = None,
                        target_chat_id: int = None) -> Optional[int]:
    """
    Sends a message to Telegram, with optional photo and buttons.
    """
    telegram_token = os.environ.get('TELEGRAM_TOKEN')
    # Default to group chat ID if no specific target is given
    chat_id = target_chat_id if target_chat_id else os.environ.get('CHAT_ID')

    if not telegram_token or not chat_id:
        logger.error("TELEGRAM_TOKEN or CHAT_ID not set.")
        return None

    endpoint = "sendPhoto" if photo_data else "sendMessage"
    url = f"https://api.telegram.org/bot{telegram_token}/{endpoint}"

    payload = {'chat_id': chat_id, 'parse_mode': 'HTML'}
    if topic_id and not target_chat_id: # Only use topic_id if sending to the main group
        payload['message_thread_id'] = topic_id
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id

    if buttons:
        inline_keyboard = [[{"text": b['text'], "url": b['url']}] for b in buttons]
        payload['reply_markup'] = json.dumps({"inline_keyboard": inline_keyboard})

    data = aiohttp.FormData()
    for k, v in payload.items():
        data.add_field(k, str(v))

    if photo_data:
        if len(message) > 1000:
            # Caption is too long, send photo and text separately
            data.add_field('photo', photo_data, filename='image.jpg')
            try:
                async with session.post(url, data=data) as resp:
                    resp.raise_for_status()
            except Exception as e:
                logger.error(f"Telegram photo send failed: {e}")

            # Now send the text
            endpoint = "sendMessage"
            url = f"https://api.telegram.org/bot{telegram_token}/{endpoint}"
            payload['text'] = message
            payload['disable_web_page_preview'] = 'true'
            data = aiohttp.FormData()
            for k, v in payload.items():
                data.add_field(k, str(v))
        else:
            data.add_field('photo', photo_data, filename='image.jpg')
            data.add_field('caption', message)
    else:
        data.add_field('text', message)
        data.add_field('disable_web_page_preview', 'true' if not buttons else 'false')

    try:
        async with session.post(url, data=data) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result.get('result', {}).get('message_id')
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return None
