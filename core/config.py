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

    # --- Telegram (Optional) ---
    TELEGRAM_TOKEN: Optional[str] = Field(None, description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: Optional[str] = Field(
        None, description="Target Chat ID", validation_alias="CHAT_ID"
    )

    # Topic Map: Site Key -> Topic ID
    # Can be JSON string or dict
    TELEGRAM_TOPIC_MAP: Dict[str, int] = Field(default_factory=dict)

    # --- Discord ---
    # Bot API (recommended)
    DISCORD_BOT_TOKEN: Optional[str] = None
    DISCORD_CHANNEL_MAP: Dict[str, str] = Field(default_factory=dict)

    # Forum Tags: Site Key -> Tag Name -> Tag ID
    DISCORD_TAG_MAP: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    # Webhook (legacy, for backward compatibility)
    DISCORD_WEBHOOK_MAP: Dict[str, str] = Field(default_factory=dict)
    DISCORD_WEBHOOK_URL: Optional[str] = None

    # --- Tag Matching Rules ---
    # Site Key -> Tag Name -> Keywords
    TAG_MATCHING_RULES: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=lambda: {
            "yu_news": {
                "긴급": ["긴급", "즉시", "필수", "중요", "마감임박", "시급"],
                "장학": ["장학", "장학금", "학자금", "등록금 감면", "포상금", "장학생"],
                "취업": [
                    "취업",
                    "채용",
                    "인턴",
                    "인턴쉽",
                    "진로",
                    "구인",
                    "면접",
                    "입사",
                    "기업",
                    "설명회",
                    "속도전",
                    "캐리어",
                ],
                "학사": [
                    "학사",
                    "수강신청",
                    "수강",
                    "학점",
                    "학기",
                    "학기말",
                    "강의",
                    "교과",
                    "휴학",
                    "복학",
                    "재수강",
                    "이수",
                    "학부",
                    "종강",
                ],
                "행사": [
                    "행사",
                    "축제",
                    "세미나",
                    "컨퍼런스",
                    "특강",
                    "워크샵",
                    "설명회",
                    "공연",
                    "공모전",
                    "대회",
                ],
                "수상/성과": [
                    "수상",
                    "선정",
                    "우수",
                    "1위",
                    "2위",
                    "3위",
                    "대상",
                    "금상",
                    "은상",
                    "동상",
                    "최우수",
                    "표창",
                    "포상",
                ],
                # "일반 공지" is default fallback
            },
            "cse_notice": {
                "긴급": [
                    "긴급",
                    "즉시",
                    "필수",
                    "중요",
                    "마감임박",
                    "시급",
                    "오늘",
                    "내일",
                ],
                "과제/시험": [
                    "과제",
                    "과제물",
                    "시험",
                    "중간고사",
                    "기말고사",
                    "평가",
                    "퀴즈",
                    "레포트",
                    "보고서",
                    "제출",
                    "시험일정",
                    "시험범위",
                ],
                "장학": ["장학", "장학금", "학자금", "장학생", "포상", "포상금"],
                "취업/진로": [
                    "취업",
                    "채용",
                    "인턴",
                    "인턴쉽",
                    "진로",
                    "구인",
                    "면접",
                    "입사",
                    "기업",
                    "설명회",
                    "속도전",
                    "캐리어",
                    "job",
                    "career",
                ],
                "학사": [
                    "학사",
                    "수강신청",
                    "수강",
                    "학점",
                    "학기",
                    "강의",
                    "교과",
                    "휴학",
                    "복학",
                    "재수강",
                    "이수",
                    "졸업",
                    "전공",
                    "복수전공",
                    "부전공",
                ],
                "행사": [
                    "행사",
                    "세미나",
                    "특강",
                    "워크샵",
                    "설명회",
                    "공연",
                    "공모전",
                    "대회",
                    "콘테스트",
                    "해커톤",
                ],
                # Default: 일반 공지 (학사)
            },
        }
    )

    # --- Available Tags per Channel (for AI Prompt) ---
    AVAILABLE_TAGS: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "yu_news": ["긴급", "장학", "취업", "학사", "행사", "수상/성과", "일반"],
            "cse_notice": ["긴급", "과제/시험", "장학", "취업/진로", "학사", "행사"],
            "bachelor_guide": [
                "시험/평가",
                "수강신청",
                "학적",
                "등록금",
                "졸업",
                "기타",
            ],
            "dormitory_notice": ["입·퇴사", "시설", "일반", "긴급"],
        }
    )

    # --- Logging ---
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    LOG_FILE: str = Field("bot.log", description="Log file path")

    # --- Scraper ---
    SCRAPE_INTERVAL: int = Field(600, description="Scraping interval in seconds")
    USER_AGENT: str = Field(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    @field_validator("TELEGRAM_TOPIC_MAP", mode="before")
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

    @field_validator("DISCORD_WEBHOOK_MAP", mode="before")
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

    @field_validator("DISCORD_CHANNEL_MAP", mode="before")
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

    @field_validator("DISCORD_TAG_MAP", mode="before")
    @classmethod
    def parse_discord_tag_map(cls, v):
        if isinstance(v, str):
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
            if not v or v.strip() == "":
                return cls.model_fields["TAG_MATCHING_RULES"].default_factory()
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"TAG_MATCHING_RULES must be valid JSON: {e}")
        return v

    def model_post_init(self, __context):
        # Backward Compatibility: If DISCORD_CHANNEL_MAP is empty but WEBHOOK_MAP exists,
        # assume user is using WEBHOOK_MAP env var for Channel IDs (as per migration plan).
        if not self.DISCORD_CHANNEL_MAP and self.DISCORD_WEBHOOK_MAP:
            self.DISCORD_CHANNEL_MAP = self.DISCORD_WEBHOOK_MAP.copy()

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

        if (
            not self.DISCORD_BOT_TOKEN
            and not self.DISCORD_WEBHOOK_MAP
            and not self.DISCORD_WEBHOOK_URL
        ):
            errors.append(
                "⚠️ No Discord configuration found - Discord notifications will be disabled"
            )

        # URL Validation (Basic)
        if self.SUPABASE_URL and not self.SUPABASE_URL.startswith("https://"):
            errors.append("❌ SUPABASE_URL must start with https://")

        return errors


settings = Settings()
