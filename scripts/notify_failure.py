import os
import sys
import json
import asyncio
import aiohttp

async def send_notification():
    token = os.getenv('DISCORD_BOT_TOKEN')
    channel_id = os.getenv('DISCORD_DEV_CHANNEL_ID')
    
    if not token or not channel_id:
        print("Missing DISCORD_BOT_TOKEN or DISCORD_DEV_CHANNEL_ID")
        return

    # Get Log Snippet
    log_snippet = os.getenv('LOG_SNIPPET', 'No logs available.')
    
    # GitHub Context
    workflow = os.getenv('GITHUB_WORKFLOW', 'Unknown')
    run_number = os.getenv('GITHUB_RUN_NUMBER', '0')
    repo = os.getenv('GITHUB_REPOSITORY', '')
    run_id = os.getenv('GITHUB_RUN_ID', '')
    server_url = os.getenv('GITHUB_SERVER_URL', 'https://github.com')
    run_url = f"{server_url}/{repo}/actions/runs/{run_id}"

    embed = {
        "title": "🚨 Bot Scraper Failed",
        "description": "The scraper job failed.",
        "color": 15158332, # Red
        "fields": [
            {
                "name": "Workflow",
                "value": workflow,
                "inline": True
            },
            {
                "name": "Run",
                "value": f"[#{run_number}]({run_url})",
                "inline": True
            },
            {
                "name": "Error Logs (Last 20 lines)",
                "value": f"```log\n{log_snippet}\n```"
            }
        ]
    }

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"embeds": [embed]}) as resp:
            if resp.status in [200, 201, 204]:
                print("Notification sent successfully.")
            else:
                print(f"Failed to send notification: {resp.status} - {await resp.text()}")

if __name__ == "__main__":
    asyncio.run(send_notification())
