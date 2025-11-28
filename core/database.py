from supabase import create_client, Client
from .config import settings
from .logger import get_logger
import time
import asyncio

logger = get_logger(__name__)

class Database:
    _instance: Client = None

    @classmethod
    def get_client(cls, max_retries: int = 3) -> Client:
        """
        Get Supabase client with retry logic.
       
        Args:
            max_retries: Maximum number of connection attempts
           
        Returns:
            Supabase Client instance
           
        Raises:
            Exception: If connection fails after all retries
        """
        if cls._instance is None:
            for attempt in range(1, max_retries + 1):
                try:
                    cls._instance = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
                    logger.info(f"[OK] Connected to Supabase (attempt {attempt}/{max_retries})")
                    
                    # Quick health check
                    try:
                        cls._instance.table('notices').select('id').limit(1).execute()
                        logger.info("[OK] Database health check passed")
                    except Exception as e:
                        logger.warning(f"Database health check warning: {e}")
                    
                    break
                except Exception as e:
                    logger.error(f"Supabase connection attempt {attempt}/{max_retries} failed: {e}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                        logger.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    else:
                        logger.critical("Failed to connect to Supabase after all retries")
                        raise Exception(f"Could not connect to Supabase: {e}")
        
        return cls._instance
    
    @classmethod
    def health_check(cls) -> bool:
        """
        Check if database connection is healthy.
       
        Returns:
            True if healthy, False otherwise
        """
        try:
            if cls._instance is None:
                return False
            cls._instance.table('notices').select('id').limit(1).execute()
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
