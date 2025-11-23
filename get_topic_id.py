import os
import requests
import time
import sys

# Force UTF-8 for stdout
sys.stdout.reconfigure(encoding='utf-8')

def get_updates(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error getting updates: {e}")
        return None

def main():
    print("=== Telegram Topic ID Finder ===")
    print("1. 봇이 있는 그룹 채팅방의 '토픽'에 아무 메시지나 보내세요.")
    print("2. 그 다음 이 스크립트를 실행하면 토픽 ID를 알려줍니다.")
    print("================================")
    
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        token = input("Enter your Telegram Bot Token: ").strip()
    
    print("\nChecking for updates...")
    updates = get_updates(token)
    
    if not updates or not updates.get('result'):
        print("No updates found. 메시지를 먼저 보내주세요!")
        return

    for update in reversed(updates['result']):
        message = update.get('message') or update.get('channel_post')
        if not message:
            continue
            
        chat = message.get('chat', {})
        topic_id = message.get('message_thread_id')
        text = message.get('text', '(No text)')
        
        print(f"\n[Message Found]")
        print(f"Chat Title: {chat.get('title')}")
        print(f"Chat ID: {chat.get('id')}")
        print(f"Topic ID (message_thread_id): {topic_id if topic_id else 'General (None)'}")
        print(f"Text: {text}")
        print("-" * 30)

if __name__ == "__main__":
    main()
