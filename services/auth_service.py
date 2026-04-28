import asyncio
from typing import Dict, Optional
from playwright.async_api import async_playwright
from core.config import settings
from core.logger import get_logger
from core import constants

logger = get_logger(__name__)

class AuthService:
    def __init__(self):
        self.login_url = constants.AUTH_SSO_EOULLIM_URL
        # The URL pattern we expect to be redirected to after successful login
        self.success_url_pattern = constants.AUTH_SUCCESS_EOULLIM_PATTERN

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

                # 2. Wait for the redesigned login form: #userId, #userPwd,
                # #btn_login (input[type=submit]). All three must exist before
                # we attempt to fill — fail fast if the page structure changed.
                try:
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)
                except Exception:
                    logger.warning("[AUTH] Form not found, reloading...")
                    await page.reload()
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)
                await page.wait_for_selector("#userPwd", state="visible", timeout=10000)
                await page.wait_for_selector("#btn_login", state="visible", timeout=10000)

                if await page.query_selector("#userId"):
                    logger.info("[AUTH] Login form ready. Filling credentials...")
                    await page.fill("#userId", settings.YU_EOULLIM_ID)
                    await page.fill("#userPwd", settings.YU_EOULLIM_PW)

                    # 3. Submit via the dedicated login button. Pressing Enter
                    # on the redesigned form did not reliably trigger the
                    # POST in headless mode. expect_navigation pairs the
                    # click with the resulting navigation atomically.
                    logger.info("[AUTH] Clicking #btn_login...")
                    try:
                        async with page.expect_navigation(timeout=20000, wait_until="domcontentloaded"):
                            await page.click("#btn_login")
                    except Exception as e:
                        logger.warning(f"[AUTH] expect_navigation did not complete cleanly: {e}")

                    # 4. Wait for the login_process POST round-trip to settle.
                    # We do NOT wait for the final destination domain — the
                    # post-login JS redirect chain (login_process →
                    # login_guide → target) frequently exceeds 20s in headless.
                    logger.info("[AUTH] Submitted. Waiting for login_process response...")
                    try:
                        await page.wait_for_url("**/login_process**", timeout=20000)
                    except Exception:
                        try:
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception as e:
                            logger.warning(f"[AUTH] networkidle fallback timed out: {e}")

                    # 5. Authentication is verified by the presence of
                    # `ssotoken` on .yu.ac.kr — the SSO portal sets this
                    # the moment credentials are accepted.
                    cookies = await context.cookies()

                    has_ssotoken = any(c.get("name") == "ssotoken" for c in cookies)
                    if not has_ssotoken:
                        logger.error("[AUTH] ssotoken cookie not set; Eoullim login failed")
                        return None

                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    logger.info(
                        f"[AUTH] Login Successful (ssotoken set). "
                        f"Retrieved {len(cookie_dict)} cookies."
                    )
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

    async def get_yutopia_cookies(self) -> Optional[Dict[str, str]]:
        """
        Performs automated login to YU SSO and returns cookies for YUtopia.
        """
        if not settings.YU_EOULLIM_ID or not settings.YU_EOULLIM_PW:
            logger.warning("[AUTH] YU_EOULLIM_ID or YU_EOULLIM_PW not set. Skipping YUtopia authentication.")
            return None

        # YUtopia specific SSO URL
        target_login_url = constants.AUTH_SSO_YUTOPIA_URL
        
        logger.info("[AUTH] Starting YUtopia SSO login process...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent=settings.USER_AGENT,
                ignore_https_errors=True,
                accept_downloads=True
            )
            page = await context.new_page()

            try:
                # 1. Navigate to SSO Login
                logger.info(f"[AUTH] Navigating to {target_login_url}")
                await page.goto(target_login_url, timeout=60000, wait_until="domcontentloaded")

                # 2. Wait for the redesigned login form: #userId, #userPwd,
                # #btn_login (input[type=submit]).
                try:
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)
                except Exception:
                    logger.warning("[AUTH] Form not found, reloading...")
                    await page.reload()
                    await page.wait_for_selector("#userId", state="visible", timeout=10000)
                await page.wait_for_selector("#userPwd", state="visible", timeout=10000)
                await page.wait_for_selector("#btn_login", state="visible", timeout=10000)

                if await page.query_selector("#userId"):
                    logger.info("[AUTH] Login form ready. Filling credentials...")
                    await page.fill("#userId", settings.YU_EOULLIM_ID)
                    await page.fill("#userPwd", settings.YU_EOULLIM_PW)

                    # 3. Submit via the dedicated login button + expect_navigation.
                    logger.info("[AUTH] Clicking #btn_login...")
                    try:
                        async with page.expect_navigation(timeout=20000, wait_until="domcontentloaded"):
                            await page.click("#btn_login")
                    except Exception as e:
                        logger.warning(f"[AUTH] expect_navigation did not complete cleanly: {e}")

                    # 4. Wait for the login_process POST round-trip to settle.
                    # We do NOT wait for yutopia.yu.ac.kr — the post-login JS
                    # redirect chain often exceeds 20s in headless mode.
                    logger.info("[AUTH] Submitted. Waiting for login_process response...")
                    try:
                        await page.wait_for_url("**/login_process**", timeout=20000)
                    except Exception:
                        try:
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception as e:
                            logger.warning(f"[AUTH] networkidle fallback timed out: {e}")

                    # 5. Authentication is verified by the presence of
                    # `ssotoken` on .yu.ac.kr — set as soon as credentials
                    # are accepted, before the redirect chain completes.
                    cookies = await context.cookies()

                    has_ssotoken = any(c.get("name") == "ssotoken" for c in cookies)
                    if not has_ssotoken:
                        logger.error("[AUTH] ssotoken cookie not set; YUtopia login failed")
                        return None

                    cookie_dict = {c['name']: c['value'] for c in cookies}
                    logger.info(
                        f"[AUTH] YUtopia Login Successful (ssotoken set). "
                        f"Retrieved {len(cookie_dict)} cookies."
                    )
                    return cookie_dict
                    
                else:
                    logger.error("[AUTH] Login page structure mismatch.")
                    return None

            except Exception as e:
                logger.error(f"[AUTH] YUtopia Login failed: {e}")
                return None
            finally:
                await browser.close()
