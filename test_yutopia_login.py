import asyncio
import os
from services.auth_service import AuthService
from services.scraper.fetcher import NoticeFetcher
from core.config import settings

async def main():
    print("--- Testing YUtopia Login and File Access ---")
    
    # Check credentials presence (don't print them)
    if not settings.YU_EOULLIM_ID or not settings.YU_EOULLIM_PW:
        print("ERROR: YU_EOULLIM_ID or YU_EOULLIM_PW not set in .env")
        return

    auth = AuthService()
    print("1. Attempting Login...")
    cookies = await auth.get_yutopia_cookies()
    
    if not cookies:
        print("FAILED: No cookies retrieved.")
        return
        
    print(f"SUCCESS: Retrieved {len(cookies)} cookies.")
    
    # 1.5 Activate Session (Critical Step based on previous failure)
    # The file download returned <script>location.href='/modules/yu/sso/loginCheck.php'</script>
    # This implies we need to hit this URL to finalize the file access session.
    print("\n1.5 Activating Session via loginCheck.php...")
    auth_check_url = "https://yutopia.yu.ac.kr/modules/yu/sso/loginCheck.php"
    
    # We use the session with cookies
    fetcher = NoticeFetcher()
    session = await fetcher.create_session()
    fetcher.set_cookies(session, cookies)
    
    try:
        async with session.get(auth_check_url) as resp:
             print(f"Session Check Status: {resp.status}")
             text = await resp.text()
             print(f"Session Check Response: {text[:100].strip()}...")
             # It might redirect or just set more cookies.
             # Update cookies from session if any new ones were set
             new_cookies = session.cookie_jar.filter_cookies(resp.url)
             if new_cookies:
                 print(f"Updated cookies: {len(new_cookies)}")
    except Exception as e:
        print(f"Session activation failed: {e}")

    # 2. Test Protected File Access
    file_url = "https://yutopia.yu.ac.kr/attachment/download/40830/%EB%B6%99%EC%9E%84.+2025+YUnicorn+%EA%B8%80%EB%A1%9C%EB%B2%8C+%EC%95%99%ED%8A%B8%EC%BA%A0%ED%94%84+MALAYSIA+%EA%B3%B5%EA%B3%A0%EB%AC%B8.pdf"
    
    # 2.2 Try WITH cookies (reusing session)
    print("\n2.2 Requesting file WITH cookies...")
    
    # Add Referer
    headers = {
        "Referer": "https://yutopia.yu.ac.kr/ko/program/all/view/20624",
        "User-Agent": settings.USER_AGENT
    }
    
    try:
        async with session.get(file_url, headers=headers, allow_redirects=True) as resp:
            print(f"Status: {resp.status}")
            content_type = resp.headers.get("Content-Type", "")
            content_length = resp.headers.get("Content-Length")
            print(f"Content-Type: {content_type}")
            print(f"Content-Length: {content_length}")
            
            content = await resp.read()
            
            # More robust binary check
            if resp.status == 200:
                if "application" in content_type or "image" in content_type or "octet-stream" in content_type:
                     print(f"PASS: Access granted. Downloaded {len(content)} bytes. Type: {content_type}")
                elif len(content) > 1000 and b"html" not in content[:50].lower():
                     print(f"PASS: Access granted (heuristic). Downloaded {len(content)} bytes.")
                else:
                     print("FAIL: Content seems to be HTML.")
                     print(f"Preview: {content[:300].decode('utf-8', errors='ignore')}")
            else:
                print("FAIL: Status not 200.")
                print(f"Preview: {content[:300].decode('utf-8', errors='ignore')}")

    except Exception as e:
        print(f"Error: {e}")
        
    await session.close()

if __name__ == "__main__":
    asyncio.run(main())
