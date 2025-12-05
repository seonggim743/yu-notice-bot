from pydantic_settings import BaseSettings
from typing import Dict, List, Optional, Union
from pydantic import Field, field_validator
import json
from core import constants


class Settings(BaseSettings):
    # --- Supabase ---
    SUPABASE_URL: str = Field(..., description="Supabase Project URL")
    SUPABASE_KEY: str = Field(..., description="Supabase Service Role Key")

    # --- AI ---
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API Key")
    GEMINI_MODEL: str = Field("gemini-2.5-flash", description="AI Model Name")

    # --- Telegram (Optional) ---
    TELEGRAM_TOKEN: Optional[str] = Field(None, description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: Optional[str] = Field(
        None, description="Target Chat ID", validation_alias="CHAT_ID"
    )
    TELEGRAM_ERROR_TOPIC_ID: Optional[int] = Field(
        None, description="Topic ID for error notifications"
    )

    # Topic Map: Site Key -> Topic ID
    # Can be JSON string or dict
    TELEGRAM_TOPIC_MAP: Union[Dict[str, int], str] = Field(default_factory=dict)

    # --- Discord ---
    # Bot API (recommended)
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_CHANNEL_MAP: Union[Dict[str, str], str] = Field(default_factory=dict)

    # Forum Tags: Site Key -> Tag Name -> Tag ID
    DISCORD_TAG_MAP: Union[Dict[str, Dict[str, str]], str] = Field(default_factory=dict)

    # Error Channel (Optional, defaults to 'dev' in CHANNEL_MAP)
    DISCORD_ERROR_CHANNEL_ID: Optional[str] = None

    # --- Tag Matching Rules ---
    # Site Key -> Tag Name -> Keywords
    TAG_MATCHING_RULES: Union[Dict[str, Dict[str, List[str]]], str] = Field(
        default_factory=lambda: constants.DEFAULT_TAG_MATCHING_RULES
    )

    # --- Available Tags per Channel (for AI Prompt) ---
    AVAILABLE_TAGS: Dict[str, List[str]] = Field(
        default_factory=lambda: constants.DEFAULT_AVAILABLE_TAGS
    )

    # --- Logging ---
    LOG_LEVEL: str = Field(constants.DEFAULT_LOG_LEVEL, description="Logging level")
    LOG_FILE: str = Field(constants.DEFAULT_LOG_FILE, description="Log file path")
    LOG_FORMAT: str = Field(constants.DEFAULT_LOG_FORMAT, description="Log format (text/json)")
    LOG_MAX_BYTES: int = Field(constants.DEFAULT_LOG_MAX_BYTES, description="Max log file size")
    LOG_BACKUP_COUNT: int = Field(constants.DEFAULT_LOG_BACKUP_COUNT, description="Log backup count")

    # --- Scraper ---
    SCRAPE_INTERVAL: int = Field(constants.DEFAULT_SCRAPE_INTERVAL, description="Scraping interval in seconds")
    USER_AGENT: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    @field_validator("TELEGRAM_TOPIC_MAP", mode="before")
    @classmethod
    def parse_telegram_topic_map(cls, v):
        if isinstance(v, str):
            v = v.strip()
            # Handle accidental copy-paste of "KEY=VALUE"
            if v.startswith("TELEGRAM_TOPIC_MAP="):
                v = v.split("=", 1)[1]
            # Handle surrounding quotes
            v = v.strip("'").strip('"')
            
            if not v or v.strip() == "":
                return {}
            try:
                parsed = json.loads(v)
                # Convert values to int
                return {k: int(val) for k, val in parsed.items()}
            except (json.JSONDecodeError, ValueError) as e:
                # If it's just a string but not JSON, maybe return empty or raise?
                # But we allow Union[Dict, str] now, so if it fails JSON, we might leave it as str?
                # No, the type hint says Union, but we want to PARSE it if possible.
                # If it fails, we raise error because we expect JSON structure.
                raise ValueError(f"TELEGRAM_TOPIC_MAP must be valid JSON: {e}")
        return v



    @field_validator("DISCORD_CHANNEL_MAP", mode="before")
    @classmethod
    def parse_discord_channel_map(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("DISCORD_CHANNEL_MAP="):
                v = v.split("=", 1)[1]
            v = v.strip("'").strip('"')

            if not v or v.strip() == "":
                return {}
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"DISCORD_CHANNEL_MAP must be valid JSON: {e}")
        return v

    @field_validator("DISCORD_TAG_MAP", mode="before")
    @classmethod
    def parse_discord_tag_map(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("DISCORD_TAG_MAP="):
                v = v.split("=", 1)[1]
            v = v.strip("'").strip('"')

            if not v or v.strip() == "":
                return {}
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"DISCORD_TAG_MAP must be valid JSON: {e}")
        return v

    @field_validator("TAG_MATCHING_RULES", mode="before")
    @classmethod
    def parse_tag_matching_rules(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("TAG_MATCHING_RULES="):
                v = v.split("=", 1)[1]
            v = v.strip("'").strip('"')

            if not v or v.strip() == "":
                return cls.model_fields["TAG_MATCHING_RULES"].default_factory()
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"TAG_MATCHING_RULES must be valid JSON: {e}")
        return v

    def model_post_init(self, __context):
        # Fallback: If TELEGRAM_ERROR_TOPIC_ID is not set, try to get 'dev' from TELEGRAM_TOPIC_MAP
        if not self.TELEGRAM_ERROR_TOPIC_ID and self.TELEGRAM_TOPIC_MAP:
            self.TELEGRAM_ERROR_TOPIC_ID = self.TELEGRAM_TOPIC_MAP.get("dev")


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
        if not self.SUPABASE_URL:
            errors.append("❌ SUPABASE_URL is missing")
        if not self.SUPABASE_KEY:
            errors.append("❌ SUPABASE_KEY is missing")
        if not self.TELEGRAM_TOKEN:
            errors.append("❌ TELEGRAM_TOKEN is missing")
        if not self.TELEGRAM_CHAT_ID:
            errors.append("❌ TELEGRAM_CHAT_ID is missing")

        # Warnings
        if not self.GEMINI_API_KEY:
            errors.append("⚠️ GEMINI_API_KEY is missing - AI features will be disabled")

        if not self.DISCORD_BOT_TOKEN:
            errors.append(
                "⚠️ DISCORD_BOT_TOKEN is missing - Discord notifications will be disabled"
            )

        # URL Validation (Basic)
        if self.SUPABASE_URL and not self.SUPABASE_URL.startswith("https://"):
            errors.append("❌ SUPABASE_URL must start with https://")

        return errors


settings = Settings()
