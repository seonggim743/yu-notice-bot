"""
Database module with Dependency Injection support.
Provides Supabase client connection with retry logic.
"""
import time
from typing import Optional
from supabase import create_client, Client

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


class DatabaseClient:
    """
    Database client wrapper with connection management.
    
    Supports Dependency Injection - create instance and pass to services.
    
    Usage:
        # In composition root (main.py)
        db_client = DatabaseClient()
        db = db_client.connect()
        
        # Inject to services
        repo = NoticeRepository(db=db)
        ai_service = AIService(db=db)
    """
    
    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        """
        Initialize DatabaseClient.
        
        Args:
            supabase_url: Supabase project URL (defaults to settings)
            supabase_key: Supabase API key (defaults to settings)
        """
        self.supabase_url = supabase_url or settings.SUPABASE_URL
        self.supabase_key = supabase_key or settings.SUPABASE_KEY
        self._client: Optional[Client] = None
    
    def connect(self, max_retries: int = 3) -> Client:
        """
        Connect to Supabase with retry logic.
        
        Args:
            max_retries: Maximum number of connection attempts
            
        Returns:
            Supabase Client instance
            
        Raises:
            Exception: If connection fails after all retries
        """
        if self._client is not None:
            return self._client
        
        for attempt in range(1, max_retries + 1):
            try:
                self._client = create_client(
                    self.supabase_url,
                    self.supabase_key
                )
                logger.info(
                    f"[OK] Connected to Supabase (attempt {attempt}/{max_retries})"
                )
                
                # Quick health check
                self._health_check_internal()
                break
                
            except Exception as e:
                logger.error(
                    f"Supabase connection attempt {attempt}/{max_retries} failed: {e}"
                )
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.critical(
                        "Failed to connect to Supabase after all retries"
                    )
                    raise Exception(f"Could not connect to Supabase: {e}")
        
        return self._client
    
    def _health_check_internal(self) -> None:
        """Internal health check during connection."""
        try:
            self._client.table("notices").select("id").limit(1).execute()
            logger.info("[OK] Database health check passed")
        except Exception as e:
            logger.warning(f"Database health check warning: {e}")
    
    def health_check(self) -> bool:
        """
        Check if database connection is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            if self._client is None:
                return False
            self._client.table("notices").select("id").limit(1).execute()
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
    
    @property
    def client(self) -> Optional[Client]:
        """Returns the Supabase client instance."""
        return self._client


# =============================================================================
# Backward Compatibility Layer
# These are kept for gradual migration - will be deprecated
# =============================================================================

class Database:
    """
    Legacy singleton Database class.
    
    DEPRECATED: Use DatabaseClient with dependency injection instead.
    This class is kept for backward compatibility during migration.
    """
    _instance: Optional[Client] = None
    _db_client: Optional[DatabaseClient] = None
    
    @classmethod
    def get_client(cls, max_retries: int = 3) -> Client:
        """
        Get Supabase client with retry logic.
        
        DEPRECATED: Use DatabaseClient.connect() with DI instead.
        """
        if cls._instance is None:
            cls._db_client = DatabaseClient()
            cls._instance = cls._db_client.connect(max_retries=max_retries)
        return cls._instance
    
    @classmethod
    def health_check(cls) -> bool:
        """
        Check if database connection is healthy.
        
        DEPRECATED: Use DatabaseClient.health_check() with DI instead.
        """
        if cls._db_client is None:
            return False
        return cls._db_client.health_check()
    
    @classmethod
    def _reset_for_testing(cls) -> None:
        """Reset singleton state for testing purposes only."""
        cls._instance = None
        cls._db_client = None
