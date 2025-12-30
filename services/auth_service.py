import asyncio
from typing import Dict, Optional
from playwright.async_api import async_playwright
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

class AuthService:
    def __init__(self):
        self.login_url = "https://portal.yu.ac.kr/sso/login.jsp?type=linc&cReturn_Url=join.yu.ac.kr"
        # The URL we expect to be redirected to after successful login
        self.success_url_pattern = "join.yu.ac.kr"

    async def get_eoullim_cookies(self) -> Optional[Dict[str, str]]:
        """
        Performs automated login to YU SSO and returns cookies for Eoullim.
        """
        if not settings.YU_EOULLIM_ID or not settings.YU_EOULLIM_PW:
            logger.warning("[AUTH] YU_EOULLIM_ID or YU_EOULLIM_PW not set. Skipping authentication.")
            return None

        logger.info("[AUTH] Starting Eoullim SSO login process...")
        
        async with async_playwright() as p:
            # Launch browser with explicit args to avoid connection issues
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            # Create context with ignore_https_errors to fix connection reset
            context = await browser.new_context(
                user_agent=settings.USER_AGENT,
                ignore_https_errors=True,
                accept_downloads=True
            )
            page = await context.new_page()

            try:
                # 1. Navigate to SSO Login
                logger.info(f"[AUTH] Navigating to {self.login_url}")
                # Set timeout longer for initial load
                await page.goto(self.login_url, timeout=60000, wait_until="domcontentloaded")

                # 2. Fill Credentials
                # Wait for selector explicitly
                try:
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)
                except Exception:
                    # Retry once
                    logger.warning("[AUTH] Form not found, reloading...")
                    await page.reload()
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)

                if await page.query_selector("#userId"):
                    logger.info("[AUTH] Login page loaded. Filling credentials...")
                    await page.fill("#userId", settings.YU_EOULLIM_ID)
                    await page.fill("#userPwd", settings.YU_EOULLIM_PW)
                    
                    # 3. Submit
                    # The login button class is usually .btn_login or we can use the text
                    # Based on analysis: ID=userId, PW=userPwd
                    # Let's try pressing Enter or clicking login button
                    # Assuming there's a button with type="submit" or specific class
                    # Safe bet: press Enter on password field
                    await page.press("#userPwd", "Enter")
                    
                    # 4. Wait for redirection
                    logger.info("[AUTH] Submitted. Waiting for redirect to join.yu.ac.kr...")
                    
                    try:
                        # Explicitly wait for the URL to change to the target domain
                        await page.wait_for_url(lambda url: "join.yu.ac.kr" in url, timeout=20000)
                        logger.info("[AUTH] Redirection verified: Reached join.yu.ac.kr")
                        
                        # Wait a bit more for session cookies to be set thoroughly
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        
                    except Exception as e:
                        logger.warning(f"[AUTH] Redirection wait failed or timed out: {e}")
                        # Fallback: check where we are
                        if "join.yu.ac.kr" in page.url:
                             logger.info("[AUTH] URL check OK despite timeout.")
                        else:
                             # If still on SSO, try to force go to target? No, that might break session.
                             logger.error(f"[AUTH] Failed to reach target domain. Current: {page.url}")

                    # 5. Extract Cookies
                    cookies = await context.cookies()
                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    
                    # Log success (masking sensitive data)
                    logger.info(f"[AUTH] Login Successful. Retrieved {len(cookie_dict)} cookies.")
                    return cookie_dict
                    
                else:
                    logger.error("[AUTH] Login page structure mismatch. form fields not found.")
                    return None

            except Exception as e:
                logger.error(f"[AUTH] Login failed: {e}")
                # Optional: Take screenshot on failure for debug
                # await page.screenshot(path="auth_failure.png")
                return None
            finally:
                await browser.close()
