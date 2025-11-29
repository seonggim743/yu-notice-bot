import os
import sys
import json
import asyncio
import aiohttp

async def send_discord(session, token, channel_id, embed):
    if not token or not channel_id: return
    
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }
    try:
        async with session.post(url, headers=headers, json={"embeds": [embed]}) as resp:
            if resp.status in [200, 201, 204]:
                print(f"Discord notification sent to {channel_id}")
            else:
                print(f"Failed to send Discord notification: {resp.status} - {await resp.text()}")
    except Exception as e:
        print(f"Discord send error: {e}")

async def send_telegram(session, token, chat_id, topic_id, message):
    if not token or not chat_id: return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if topic_id:
        payload["message_thread_id"] = topic_id
        
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                print(f"Telegram notification sent to {topic_id}")
            else:
                print(f"Failed to send Telegram notification: {resp.status} - {await resp.text()}")
    except Exception as e:
        print(f"Telegram send error: {e}")

async def main():
    # 1. Load Config & Maps
    discord_token = os.getenv('DISCORD_BOT_TOKEN')
    telegram_token = os.getenv('TELEGRAM_TOKEN')
    telegram_chat_id = os.getenv('CHAT_ID')
    
    # Parse Maps
    discord_map = {}
    # Try CHANNEL_MAP first (New standard)
    try:
        discord_map = json.loads(os.getenv('DISCORD_CHANNEL_MAP', '{}'))
    except: pass
    
    # Fallback to WEBHOOK_MAP (Legacy/User preference) if empty or dev key missing
    if not discord_map.get('dev'):
        try:
            webhook_map = json.loads(os.getenv('DISCORD_WEBHOOK_MAP', '{}'))
            discord_map.update(webhook_map)
        except: pass
    
    telegram_map = {}
    try:
        telegram_map = json.loads(os.getenv('TELEGRAM_TOPIC_MAP', '{}'))
    except: pass
    
    # Determine Targets (Map 'dev' key > Env Var)
    discord_channel_id = discord_map.get('dev') or os.getenv('DISCORD_DEV_CHANNEL_ID')
    telegram_topic_id = telegram_map.get('dev') or os.getenv('TELEGRAM_DEV_TOPIC_ID')
    
    if not discord_channel_id and not telegram_topic_id:
        print("No dev channels configured (check maps or env vars).")
        return

    # 2. Prepare Content
    log_snippet = os.getenv('LOG_SNIPPET', 'No logs available.')
    workflow = os.getenv('GITHUB_WORKFLOW', 'Unknown')
    run_number = os.getenv('GITHUB_RUN_NUMBER', '0')
    repo = os.getenv('GITHUB_REPOSITORY', '')
    run_id = os.getenv('GITHUB_RUN_ID', '')
    server_url = os.getenv('GITHUB_SERVER_URL', 'https://github.com')
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}"

    # Discord Embed
    discord_embed = {
        "title": "🚨 Bot Scraper Failed",
        "description": "The scraper job failed.",
        "color": 15158332,
        "fields": [
            { "name": "Workflow", "value": workflow, "inline": True },
            { "name": "Run", "value": f"[#{run_number}]({run_url})", "inline": True },
            { "name": "Error Logs (Last 20 lines)", "value": f"```log\n{log_snippet}\n```" }
        ]
    }
    
    # Telegram Message
    # Escape HTML special chars in log snippet for Telegram
    import html
    safe_log_snippet = html.escape(log_snippet)
    telegram_msg = (
        f"🚨 <b>Bot Scraper Failed</b>\n\n"
        f"Workflow: {workflow}\n"
        f"Run: <a href='{run_url}'>#{run_number}</a>\n\n"
        f"<b>Error Logs:</b>\n<pre>{safe_log_snippet}</pre>"
    )

    # 3. Send Notifications
    async with aiohttp.ClientSession() as session:
        tasks = []
        if discord_token and discord_channel_id:
            tasks.append(send_discord(session, discord_token, discord_channel_id, discord_embed))
        
        if telegram_token and telegram_chat_id and telegram_topic_id:
            tasks.append(send_telegram(session, telegram_token, telegram_chat_id, telegram_topic_id, telegram_msg))
            
        if tasks:
            await asyncio.gather(*tasks)
        else:
            print("No valid notification targets found.")

if __name__ == "__main__":
    asyncio.run(main())
