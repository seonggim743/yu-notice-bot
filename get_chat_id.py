import requests
import sys

# Force UTF-8 for stdout
sys.stdout.reconfigure(encoding='utf-8')

def get_chat_id(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    print(f"Checking updates for bot...")
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        if not data['ok']:
            print(f"❌ Error: {data.get('description')}")
            return

        results = data.get('result', [])
        if not results:
            print("⚠️ 메시지가 없습니다. 텔레그램 봇에게 아무 메시지나(예: 'hello') 보내고 다시 실행해주세요.")
            return

        # Get the most recent message
        last_update = results[-1]
        chat_id = last_update['message']['chat']['id']
        username = last_update['message']['chat'].get('username', 'Unknown')
        
        print("\n✅ Chat ID를 찾았습니다!")
        print(f"User: {username}")
        print(f"Chat ID: {chat_id}")
        print("------------------------------------------------")
        print(f"이 숫자를 CHAT_ID로 사용하세요: {chat_id}")
        
    except Exception as e:
        print(f"❌ 실패: {e}")

if __name__ == "__main__":
    print("텔레그램 Chat ID 찾기 도구")
    print("-----------------------")
    token = input("Bot Token을 입력하세요: ").strip()
    
    if token:
        get_chat_id(token)
    else:
        print("Token이 필요합니다.")
