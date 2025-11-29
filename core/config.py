from pydantic_settings import BaseSettings
from typing import Dict, List, Optional
from pydantic import Field, field_validator
import json

class Settings(BaseSettings):
    # --- Supabase ---
    SUPABASE_URL: str = Field(..., description="Supabase Project URL")
    SUPABASE_KEY: str = Field(..., description="Supabase Service Role Key")

    # --- AI ---
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API Key")
    GEMINI_MODEL: str = Field("gemini-2.5-flash", description="AI Model Name")

    # --- Telegram ---
    TELEGRAM_TOKEN: str = Field(..., description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: str = Field(..., description="Target Chat ID", validation_alias="CHAT_ID")
    
    # Topic Map: Site Key -> Topic ID
    # Can be JSON string or dict
    TELEGRAM_TOPIC_MAP: Dict[str, int] = Field(default_factory=dict)

    # --- Discord ---
    # Bot API (recommended)
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_CHANNEL_MAP: Dict[str, str] = Field(default_factory=dict)
    
    # Webhook (legacy, for backward compatibility)
    DISCORD_WEBHOOK_MAP: Dict[str, str] = Field(default_factory=dict)
    DISCORD_WEBHOOK_URL: Optional[str] = None

    # --- Logging ---
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    LOG_FILE: str = Field("bot.log", description="Log file path")

    # --- Scraper ---
    SCRAPE_INTERVAL: int = Field(600, description="Scraping interval in seconds")
    USER_AGENT: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    @field_validator('TELEGRAM_TOPIC_MAP', mode='before')
    @classmethod
    def parse_telegram_topic_map(cls, v):
        if isinstance(v, str):
            if not v or v.strip() == "":
                return {}
            try:
                parsed = json.loads(v)
                # Convert values to int
                return {k: int(val) for k, val in parsed.items()}
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"TELEGRAM_TOPIC_MAP must be valid JSON: {e}")
        return v

    @field_validator('DISCORD_WEBHOOK_MAP', mode='before')
    @classmethod
    def parse_discord_webhook_map(cls, v):
        if isinstance(v, str):
            if not v or v.strip() == "":
                return {}
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"DISCORD_WEBHOOK_MAP must be valid JSON: {e}")
        return v
    
    @field_validator('DISCORD_CHANNEL_MAP', mode='before')
    @classmethod
    def parse_discord_channel_map(cls, v):
        if isinstance(v, str):
            if not v or v.strip() == "":
                return {}
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"DISCORD_CHANNEL_MAP must be valid JSON: {e}")
        return v

    def model_post_init(self, __context):
        # Fallback: If map is empty but single URL exists, use it for known keys
        if not self.DISCORD_WEBHOOK_MAP and self.DISCORD_WEBHOOK_URL:
            # Default keys we know of
            self.DISCORD_WEBHOOK_MAP = {
                "yu_news": self.DISCORD_WEBHOOK_URL,
                "cse_notice": self.DISCORD_WEBHOOK_URL
            }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

    def validate_all(self) -> List[str]:
        """
        Validate all configuration settings.
        Returns a list of warning/error messages.
        """
        errors = []
        
        # Critical
        if not self.SUPABASE_URL: errors.append("❌ SUPABASE_URL is missing")
        if not self.SUPABASE_KEY: errors.append("❌ SUPABASE_KEY is missing")
        if not self.TELEGRAM_TOKEN: errors.append("❌ TELEGRAM_TOKEN is missing")
        if not self.TELEGRAM_CHAT_ID: errors.append("❌ TELEGRAM_CHAT_ID is missing")
        
        # Warnings
        if not self.GEMINI_API_KEY:
            errors.append("⚠️ GEMINI_API_KEY is missing - AI features will be disabled")
            
        if not self.DISCORD_BOT_TOKEN and not self.DISCORD_WEBHOOK_MAP and not self.DISCORD_WEBHOOK_URL:
            errors.append("⚠️ No Discord configuration found - Discord notifications will be disabled")
            
        # URL Validation (Basic)
        if self.SUPABASE_URL and not self.SUPABASE_URL.startswith("https://"):
            errors.append("❌ SUPABASE_URL must start with https://")
            
        return errors

settings = Settings()
