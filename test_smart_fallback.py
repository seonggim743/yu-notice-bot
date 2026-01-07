
import asyncio
import logging
from services.ai_service import AIService

# Configure logging
logging.basicConfig(level=logging.INFO)

async def test_smart_fallback():
    service = AIService()
    
    print("\n[TEST] 1. Fetching available models...")
    models = await service._get_available_models()
    print(f"Available Models: {models}")
    
    if not models:
        print("❌ No models available. Check DB migration.")
        return

    print("\n[TEST] 2. Running Analysis (Should try models in order)...")
    # Using a dummy notice text
    result = await service.analyze_notice(
        text="This is a test notice about scholarship. Important deadline is tomorrow.",
        title="Test Scholarship Notice",
        author="Student Team"
    )
    
    print(f"\n[TEST] Result: {result}")

if __name__ == "__main__":
    asyncio.run(test_smart_fallback())
