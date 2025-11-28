import asyncio
import signal
import sys
from core.config import settings
from core.logger import get_logger
from core.database import Database
from core.error_notifier import get_error_notifier, ErrorSeverity
from services.scraper_service import ScraperService

logger = get_logger(__name__)

class Bot:
    def __init__(self):
        self.scraper = ScraperService()
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
            db = Database.get_client()
            if not Database.health_check():
                logger.critical("Database health check failed")
                return False
        except Exception as e:
            logger.critical(f"Database connection failed: {e}")
            # Send error notification
            asyncio.create_task(
                get_error_notifier().send_critical_error(
                    "Database connection failed during startup",
                    exception=e,
                    severity=ErrorSeverity.CRITICAL
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
            if sys.platform != 'win32':
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
            except Exception as e:
                self.error_count += 1
                logger.critical(f"Critical Error in Main Loop ({self.error_count}/{self.MAX_CONSECUTIVE_ERRORS}): {e}", exc_info=True)
                
                # Send error notification
                asyncio.create_task(
                    get_error_notifier().send_critical_error(
                        f"Critical error in main loop (attempt {self.error_count}/{self.MAX_CONSECUTIVE_ERRORS})",
                        exception=e,
                        context={"error_count": self.error_count, "max_errors": self.MAX_CONSECUTIVE_ERRORS},
                        severity=ErrorSeverity.CRITICAL
                    )
                )
                
                if self.error_count >= self.MAX_CONSECUTIVE_ERRORS:
                    logger.critical(f"Too many consecutive errors. Shutting down.")
                    # Final shutdown notification
                    asyncio.create_task(
                        get_error_notifier().send_critical_error(
                            "Bot shutting down due to repeated failures",
                            context={"consecutive_errors": self.error_count},
                            severity=ErrorSeverity.CRITICAL
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

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Yu Notice Bot V2")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    bot = Bot()
    exit_code = 0
    
    if args.once:
        # Run once logic
        try:
            logger.info("Running in --once mode")
            asyncio.run(bot.scraper.run())
            logger.info("Run completed successfully")
        except Exception as e:
            logger.critical(f"Run failed: {e}", exc_info=True)
            # Send error notification
            asyncio.run(
                get_error_notifier().send_critical_error(
                    "Bot run failed in --once mode",
                    exception=e,
                    severity=ErrorSeverity.CRITICAL
                )
            )
            exit_code = 1
    else:
        try:
            asyncio.run(bot.start())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.critical(f"Fatal error: {e}", exc_info=True)
            exit_code = 1
    
    sys.exit(exit_code)
