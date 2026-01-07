
import asyncio
import os
import google.generativeai as genai
from core.config import settings

# Force the model to what the user says is valid
MODEL_NAME = "gemini-2.5-flash"

async def test_gemini():
    print(f"Testing Gemini API with model: {MODEL_NAME}")
    api_key = settings.GEMINI_API_KEY
    print(f"API Key present: {bool(api_key)}")
    
    if not api_key:
        print("❌ No API Key found.")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL_NAME)
    
    print("Sending test request...")
    try:
        response = await model.generate_content_async("Hello, can you confirm you are working?")
        print(f"✅ Success! Response: {response.text}")
        if response.usage_metadata:
             print(f"Usage: {response.usage_metadata}")
    except Exception as e:
        print(f"[FAIL]")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_gemini())
