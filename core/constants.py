# =============================================================================
# URL Constants (Authentication & External Resources)
# =============================================================================

# YU Authentication URLs
AUTH_SSO_EOULLIM_URL = "https://portal.yu.ac.kr/sso/login.jsp?type=linc&cReturn_Url=join.yu.ac.kr"
AUTH_SSO_YUTOPIA_URL = "https://portal.yu.ac.kr/sso/login.jsp?type=linc&cReturn_Url=https%3A%2F%2Fyutopia.yu.ac.kr%2Fmodules%2Fyu%2Fsso%2FloginCheck.php"
AUTH_SUCCESS_EOULLIM_PATTERN = "join.yu.ac.kr"
AUTH_SUCCESS_YUTOPIA_PATTERN = "yutopia.yu.ac.kr"

# Session Warmup URLs
YUTOPIA_SESSION_WARMUP_URL = "https://yutopia.yu.ac.kr/modules/yu/sso/loginCheck.php"

# School Resources
SCHOOL_LOGO_URL = "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png"

# Category Icon URLs (for Discord Thumbnails)
# Using Flaticon CDN for consistent category icons
CATEGORY_ICON_URLS = {
    "긴급": "https://cdn-icons-png.flaticon.com/512/595/595067.png",
    "장학": "https://cdn-icons-png.flaticon.com/512/3135/3135706.png",
    "학사": "https://cdn-icons-png.flaticon.com/512/3976/3976625.png",
    "취업": "https://cdn-icons-png.flaticon.com/512/3281/3281307.png",
    "행사": "https://cdn-icons-png.flaticon.com/512/3176/3176366.png",
    "과제/시험": "https://cdn-icons-png.flaticon.com/512/2965/2965358.png",
    "수상/성과": "https://cdn-icons-png.flaticon.com/512/3135/3135783.png",
    "생활관": "https://cdn-icons-png.flaticon.com/512/1946/1946488.png",
    "일반": "https://www.yu.ac.kr/_res/yu/kr/img/common/logo.png",
}

# Site Name Mappings (Localization)
SITE_NAME_MAP = {
    "yu_news": "영대소식",
    "cse_notice": "컴공공지",
    "bachelor_guide": "학사안내",
    "calendar": "학사일정",
    "dormitory_notice": "생활관공지",
    "dormitory_menu": "기숙사식단",
    "eoullim_career": "이음림커리어",
    "eoullim_external": "이음림대외활동",
    "eoullim_study": "이음림스터디",
    "yutopia": "유토피아",
}

# Category Emoji Mappings
CATEGORY_EMOJIS = {
    "긴급": "🚨",
    "장학": "💰",
    "학사": "🎓",
    "취업": "💼",
    "행사": "🎉",
    "과제/시험": "📝",
    "수상/성과": "🏆",
    "생활관": "🏠",
    "일반": "📢",
}

# Category Color Mappings (for Discord Embeds - Hex values)
CATEGORY_COLORS = {
    "긴급": 0xFF0000,  # 🔴 Red
    "장학": 0xFFD700,  # 💰 Gold
    "학사": 0x0099FF,  # 🎓 Blue
    "취업": 0x9B59B6,  # 💼 Purple
    "행사": 0x2ECC71,  # 🎉 Green
    "과제/시험": 0xE74C3C,  # 📝 Red-Orange
    "수상/성과": 0xF39C12,  # 🏆 Orange
    "생활관": 0x1ABC9C,  # 🏠 Turquoise
    "일반": 0x95A5A6,  # 📢 Grey
}

# =============================================================================
# Scraper Settings
# =============================================================================
MAX_AI_SUMMARIES = 50
AI_CALL_DELAY = 7.0  # Seconds between AI calls
NOTICE_PROCESS_DELAY = 0.5  # Seconds between processing notices
MAX_PREVIEWS = 10  # Maximum number of previews to generate per run

# Short Notice Thresholds
SHORT_NOTICE_CONTENT_LENGTH = 100
SHORT_NOTICE_ATTACHMENT_LENGTH = 50

# =============================================================================
# Notification Settings
# =============================================================================
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50 MB
DISCORD_MAX_EMBED_LENGTH = 1024
DISCORD_FILE_SIZE_LIMIT = 25 * 1024 * 1024  # 25 MB
FILENAME_TRUNCATE_LENGTH = 20

# =============================================================================
# AI Settings
# =============================================================================
AI_TEXT_TRUNCATE_LIMIT = 8000

# =============================================================================
# File Extension Emojis
# =============================================================================
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
    "hwp": "📄",
    "hwpx": "📄",
    "jpg": "🖼️",
    "jpeg": "🖼️",
    "png": "🖼️",
    "gif": "🖼️",
    "default": "📄",
}

# =============================================================================
# Default Configuration Values
# =============================================================================
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
    "yutopia": ["취업/진로", "비교과/활동", "행사/특강", "학습/교육", "공모전", "장학/혜택"],
}

DEFAULT_CATEGORY_MAP = {
    "eoullim_career": ["특강", "교육", "상담", "캠프", "모의시험"],
    "eoullim_external": ["공모전", "대외활동", "봉사", "인턴", "채용", "교육"],
    "eoullim_study": ["어학", "자격증", "면접", "직무", "기타"],
    "yutopia": ["취업/진로", "비교과/활동", "행사/특강", "학습/교육", "공모전", "장학/혜택"],
    "default": ["학사", "장학", "행사", "채용", "일반", "비교과"],
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
            "해커톤",
        ],
    },
    "yutopia": {
        "취업/진로": ["취업", "진로", "인턴", "채용", "상담", "설명회"],
        "비교과/활동": ["비교과", "봉사", "대외활동", "서포터즈", "동아리"],
        "행사/특강": ["행사", "특강", "캠프", "워크샵", "세미나"],
        "학습/교육": ["학습", "교육", "어학", "자격증", "시험"],
        "공모전": ["공모전", "대회", "경진대회", "콘테스트"],
        "장학/혜택": ["장학", "장학금", "혜택", "마일리지", "포인트"],
    },
}

