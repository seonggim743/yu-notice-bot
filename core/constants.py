# Scraper Settings
MAX_AI_SUMMARIES = 30
AI_CALL_DELAY = 7.0  # Seconds between AI calls
NOTICE_PROCESS_DELAY = 0.5  # Seconds between processing notices
MAX_PREVIEWS = 10  # Maximum number of previews to generate per run

# Short Notice Thresholds
SHORT_NOTICE_CONTENT_LENGTH = 100
SHORT_NOTICE_ATTACHMENT_LENGTH = 50

# Notification Settings
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50 MB
DISCORD_MAX_EMBED_LENGTH = 1024
DISCORD_FILE_SIZE_LIMIT = 25 * 1024 * 1024  # 25 MB
FILENAME_TRUNCATE_LENGTH = 20

# AI Settings
AI_TEXT_TRUNCATE_LIMIT = 8000

# File Extension Emojis
FILE_EMOJI_MAP = {
    "pdf": "📕",
    "doc": "📘",
    "docx": "📘",
    "xls": "📗",
    "xlsx": "📗",
    "ppt": "📙",
    "pptx": "📙",
    "zip": "📦",
    "rar": "📦",
    "jpg": "🖼️",
    "jpeg": "🖼️",
    "png": "🖼️",
    "gif": "🖼️",
    "default": "📄",
}

# Default Configuration Values
DEFAULT_SCRAPE_INTERVAL = 600
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FILE = "bot.log"
DEFAULT_LOG_FORMAT = "text"  # text or json
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
DEFAULT_LOG_BACKUP_COUNT = 5

DEFAULT_AVAILABLE_TAGS = {
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

DEFAULT_TAG_MATCHING_RULES = {
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
    },
}

