import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Simulate malformed env var (Prefix included + Quotes)
os.environ["DISCORD_TAG_MAP"] = "DISCORD_TAG_MAP='{\"test\": {\"tag\": \"123\"}}'"
os.environ["SUPABASE_URL"] = "https://example.com"
os.environ["SUPABASE_KEY"] = "dummy"
os.environ["TELEGRAM_TOKEN"] = "dummy"
os.environ["TELEGRAM_CHAT_ID"] = "dummy"
os.environ["GEMINI_API_KEY"] = "dummy"

try:
    from core.config import Settings
    settings = Settings()
    print("✅ Settings initialized successfully")
    print(f"DISCORD_TAG_MAP type: {type(settings.DISCORD_TAG_MAP)}")
    print(f"DISCORD_TAG_MAP value: {settings.DISCORD_TAG_MAP}")
    
    expected = {"test": {"tag": "123"}}
    if settings.DISCORD_TAG_MAP == expected:
        print("✅ Malformed string parsed correctly")
    else:
        print(f"❌ Parsing failed. Expected {expected}, got {settings.DISCORD_TAG_MAP}")
        
except Exception as e:
    print(f"❌ Failed to initialize Settings: {e}")
    import traceback
    traceback.print_exc()
