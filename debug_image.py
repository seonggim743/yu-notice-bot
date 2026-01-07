
import aiohttp
import asyncio

URL = "https://ci3.googleusercontent.com/meips/ADKq_NYW0cTxF03QE88uPRoUwYiZUNEss_o42OkVSyFTRLawGcBf3sNBVq8BAHtKxMG46_8G8gA1x8fQ88urT5AgjAnd_hhFB2Q0x50XWtqq063IvTRbs0Qq=s0-d-e1-ft#https://kita.net/editordata/2025/11/20251119_095518070_71076.png"
REFERER = "https://join.yu.ac.kr/front_new/index.php?g_page=program&m_page=program04"

async def test_download():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": REFERER
    }
    
    print(f"Downloading {URL}...")
    async with aiohttp.ClientSession() as session:
        async with session.get(URL, headers=headers) as resp:
            print(f"Status: {resp.status}")
            print(f"Content-Type: {resp.headers.get('Content-Type')}")
            print(f"Content-Length: {resp.headers.get('Content-Length')}")
            
            data = await resp.read()
            print(f"Downloaded bytes: {len(data)}")
            
            with open("debug_image_download.png", "wb") as f:
                f.write(data)
            print("Saved to debug_image_download.png")

if __name__ == "__main__":
    asyncio.run(test_download())
