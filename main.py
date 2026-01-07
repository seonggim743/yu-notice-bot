"""
Yu Notice Bot V2 - Main Entry Point (Composition Root)

This module serves as the Composition Root for the application.
All dependencies are created here and injected into services.
"""
import asyncio
import signal
import sys
import os

# 1. Setup Logging First (to capture config errors)
from core.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

# 2. Load Config
try:
    from core.config import settings
except Exception as e:
    logger.critical(f"Failed to load configuration: {e}", exc_info=True)
    sys.exit(1)

# Debug: Verify Config Loading (safe logging)
logger.debug(f"CWD = {os.getcwd()}")
logger.debug(f"Discord Token configured: {'Yes' if settings.DISCORD_BOT_TOKEN else 'No'}")

# 3. Import Dependencies
from core.database import Database, DatabaseClient
from core.error_notifier import ErrorNotifier, ErrorSeverity, set_error_notifier, get_error_notifier
from core.exceptions import (
    NetworkException,
    ScraperException,
    NotificationException,
    DatabaseException,
)
from services.scraper_service import ScraperService


class Bot:
    """
    Main Bot class that orchestrates the scraping loop.
    
    The Bot uses ScraperService which internally manages:
    - TargetManager: Target loading and filtering
    - HashCalculator: Content hashing for change detection
    - ChangeDetector: Modification detection with AI diff summaries
    - AttachmentProcessor: File download, text extraction, preview generation
    
    Supports dependency injection for testability.
    """
    
    def __init__(
        self,
        init_mode: bool = False,
        no_ai_mode: bool = False,
        scraper: ScraperService = None,
        error_notifier: ErrorNotifier = None,
    ):
        """
        Initialize the Bot.
        
        Args:
            init_mode: If True, seeds database without notifications
            no_ai_mode: If True, skips AI analysis
            scraper: Optional custom ScraperService instance (for DI/testing)
            error_notifier: Optional ErrorNotifier instance (for DI/testing)
        """
        self.scraper = scraper or ScraperService(
            init_mode=init_mode,
            no_ai_mode=no_ai_mode
        )
        self.error_notifier = error_notifier or get_error_notifier()
        self.running = True
        self.error_count = 0
        self.MAX_CONSECUTIVE_ERRORS = 5

    async def validate_startup(self) -> bool:
        """Validate system requirements before starting"""
        logger.info("=" * 60)
        logger.info("Yu Notice Bot V2 - Starting Up")
        logger.info("=" * 60)

        # Check database connection
        try:
            Database.get_client()
            if not Database.health_check():
                logger.critical("Database health check failed")
                return False
        except Exception as e:
            logger.critical(f"Database connection failed: {e}")
            # Send error notification
            asyncio.create_task(
                self.error_notifier.send_critical_error(
                    "Database connection failed during startup",
                    exception=e,
                    severity=ErrorSeverity.CRITICAL,
                )
            )
            return False

        # Check configuration
        logger.info(f"Model: {settings.GEMINI_MODEL}")
        logger.info(f"Interval: {settings.SCRAPE_INTERVAL}s")
        logger.info(f"Log Level: {settings.LOG_LEVEL}")

        validation_errors = settings.validate_all()
        for msg in validation_errors:
            if "❌" in msg:
                logger.critical(msg)
            else:
                logger.warning(msg)

        if any("❌" in msg for msg in validation_errors):
            logger.critical("Configuration validation failed")
            return False

        logger.info("[OK] Startup validation passed")
        return True

    async def start(self):
        # Validate startup
        if not await self.validate_startup():
            logger.critical("Startup validation failed. Exiting...")
            sys.exit(1)

        # Windows-compatible signal handling
        try:
            loop = asyncio.get_running_loop()
            if sys.platform != "win32":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, self.stop)
            else:
                # Windows: signal handlers work differently
                signal.signal(signal.SIGINT, lambda s, f: self.stop())
                signal.signal(signal.SIGTERM, lambda s, f: self.stop())
        except Exception as e:
            logger.warning(f"Could not set up signal handlers: {e}")

        logger.info("Bot started. Press Ctrl+C to stop.")
        logger.info("=" * 60)

        while self.running:
            try:
                await self.scraper.run()
                self.error_count = 0  # Reset on successful run

            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt")
                break
            except NetworkException as e:
                self.error_count += 1
                logger.error(
                    f"Network Error ({self.error_count}/{self.MAX_CONSECUTIVE_ERRORS}): {e}"
                )

                # Network errors are retryable - don't send notification immediately
                if self.error_count >= 3:
                    asyncio.create_task(
                        self.error_notifier.send_critical_error(
                            f"Repeated network errors ({self.error_count})",
                            exception=e,
                            severity=ErrorSeverity.WARNING,
                        )
                    )
            except (ScraperException, NotificationException, DatabaseException) as e:
                self.error_count += 1
                logger.error(
                    f"Bot Error ({self.error_count}/{self.MAX_CONSECUTIVE_ERRORS}): {type(e).__name__}: {e}"
                )

                asyncio.create_task(
                    self.error_notifier.send_critical_error(
                        f"{type(e).__name__} in main loop",
                        exception=e,
                        context={"error_count": self.error_count, "details": e.details},
                        severity=ErrorSeverity.ERROR,
                    )
                )
            except Exception as e:
                self.error_count += 1
                logger.critical(
                    f"Unexpected Error ({self.error_count}/{self.MAX_CONSECUTIVE_ERRORS}): {e}",
                    exc_info=True,
                )

                # Send error notification
                asyncio.create_task(
                    self.error_notifier.send_critical_error(
                        f"Unexpected error in main loop (attempt {self.error_count}/{self.MAX_CONSECUTIVE_ERRORS})",
                        exception=e,
                        context={
                            "error_count": self.error_count,
                            "max_errors": self.MAX_CONSECUTIVE_ERRORS,
                        },
                        severity=ErrorSeverity.CRITICAL,
                    )
                )

                if self.error_count >= self.MAX_CONSECUTIVE_ERRORS:
                    logger.critical("Too many consecutive errors. Shutting down.")
                    # Final shutdown notification
                    asyncio.create_task(
                        self.error_notifier.send_critical_error(
                            "Bot shutting down due to repeated failures",
                            context={"consecutive_errors": self.error_count},
                            severity=ErrorSeverity.CRITICAL,
                        )
                    )
                    break

            if self.running:
                logger.info(f"Sleeping for {settings.SCRAPE_INTERVAL}s...")
                try:
                    await asyncio.sleep(settings.SCRAPE_INTERVAL)
                except asyncio.CancelledError:
                    logger.info("Sleep cancelled")
                    break

        self.stop()
        logger.info("Bot stopped cleanly")

    def stop(self):
        if self.running:
            logger.info("=" * 60)
            logger.info("Stopping Bot...")
            logger.info("=" * 60)
            self.running = False


# =============================================================================
# Composition Root - Dependency Assembly
# =============================================================================

def create_dependencies():
    """
    Creates and wires all application dependencies.
    
    This is the Composition Root pattern - all dependency creation
    happens here, making the dependency graph explicit and testable.
    
    Returns:
        Dict with all created dependencies
    """
    # Import notification components here to avoid circular imports
    from services.notification.telegram import TelegramNotifier
    from services.notification.discord import DiscordNotifier
    from services.notification_service import NotificationService
    
    # 1. Create Error Notifier (needed early for error reporting)
    error_notifier = ErrorNotifier()
    set_error_notifier(error_notifier)  # Set global for backward compatibility
    
    # 2. Create Notification Channels (Strategy Pattern)
    notification_channels = [
        TelegramNotifier(),
        DiscordNotifier(),
    ]
    
    # 3. Create NotificationService with injected channels
    notification_service = NotificationService(channels=notification_channels)
    
    # 4. Database will be lazily initialized on first use
    # (Database.get_client() handles connection with retry)
    
    logger.debug("[COMPOSITION_ROOT] Dependencies created")
    logger.debug(f"[COMPOSITION_ROOT] Notification channels: {[ch.channel_name for ch in notification_channels]}")
    
    return {
        "error_notifier": error_notifier,
        "notification_channels": notification_channels,
        "notification_service": notification_service,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Yu Notice Bot V2")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize DB without notifications (Seeding)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Run scraper without AI analysis (notifications enabled)",
    )
    parser.add_argument(
        "--test-url",
        type=str,
        help="Test notification for a specific URL (forces notification)",
    )
    parser.add_argument(
        "--target",
        type=str,
        help="Run scraper for a specific target only (e.g., yu_news)",
    )
    args = parser.parse_args()

    # ==========================================================================
    # Composition Root: Create all dependencies
    # ==========================================================================
    deps = create_dependencies()
    error_notifier = deps["error_notifier"]
    
    # Create Bot with injected dependencies
    bot = Bot(
        init_mode=args.init,
        no_ai_mode=args.no_ai,
        error_notifier=error_notifier,
    )
    
    # Apply target filter if specified
    if args.target:
        bot.scraper.filter_targets(args.target)
    exit_code = 0

    if args.init:
        logger.info("🚀 Starting in INIT MODE (Database Seeding)")
        logger.info("AI analysis and Notifications will be DISABLED.")

    if args.once or args.init or args.test_url:
        # Run once logic
        try:
            if args.once:
                logger.info("Running in --once mode")

            if args.test_url:
                logger.info(f"🧪 Running Test Notification for: {args.test_url}")
                asyncio.run(bot.scraper.run_test(args.test_url))
            else:
                success = asyncio.run(bot.scraper.run())
                if not success:
                    exit_code = 1

            logger.info("Run completed successfully")
        except Exception as e:
            logger.critical(f"Run failed: {e}", exc_info=True)
            # Send error notification
            asyncio.run(
                error_notifier.send_critical_error(
                    "Bot run failed in --once/--init mode",
                    exception=e,
                    severity=ErrorSeverity.CRITICAL,
                )
            )
            exit_code = 1
    else:
        try:
            # Infinite loop mode
            asyncio.run(bot.start())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.critical(f"Fatal error: {e}", exc_info=True)
            exit_code = 1

    sys.exit(exit_code)
