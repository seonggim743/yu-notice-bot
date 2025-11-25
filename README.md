# 🤖 YU Notice Bot (Cloud & Local)

영남대학교 공지사항 및 LMS 과제 알림 봇입니다.

## 🚀 기능
1.  **공지사항 알림**: 학교 홈페이지, 학사 공지, 기숙사 식단 등을 크롤링하여 텔레그램으로 전송합니다.
2.  **LMS 과제 알림**: Canvas LMS에서 미제출 과제와 새로운 공지사항을 확인하여 알림을 보냅니다.

## 아키텍처
- `scraper.py`: 메인 Scraper입니다. `config.json`에 정의된 웹사이트의 공지사항을 크롤링하고, 새로운 게시물이 발견되면 AI를 이용해 요약한 후 텔레그램으로 전송합니다.
- `lms_scraper.py`: Canvas LMS Scraper입니다. LMS API를 이용해 새로운 과제와 공지사항을 확인하고 텔레그램으로 알림을 보냅니다.
- `telegram_client.py`: 텔레그램 메시지 전송을 위한 중앙 클라이언트입니다.
- `config.json`: Scraper가 크롤링할 웹사이트, AI 프롬프트, 텔레그램 토픽 ID 등 봇의 설정을 관리합니다.
- `models.py`: 봇에서 사용되는 데이터 모델을 정의합니다.
- `Supabase`: 크롤링한 공지사항, AI가 생성한 요약, 봇의 상태 등 모든 데이터를 저장하고 관리합니다.

## ☁️ 클라우드 실행 (Github Actions)
이 봇은 Github Actions를 통해 매시간 자동으로 실행됩니다.

### 설정 방법 (Github Secrets)
Github 저장소의 `Settings` -> `Secrets and variables` -> `Actions`에 아래 변수들을 등록해야 합니다.

| 변수명 | 설명 |
| :--- | :--- |
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 |
| `CHAT_ID` | 알림 받을 채팅방 ID |
| `GEMINI_API_KEY` | AI 요약을 위한 Google Gemini API 키 |
| `SUPABASE_URL` | 상태 저장을 위한 Supabase URL |
| `SUPABASE_KEY` | Supabase Service Role Key (또는 Anon Key) |
| `CANVAS_TOKEN` | LMS(Canvas) 액세스 토큰 |

## ⚠️ 주의사항
- **개인용**: 이 봇은 개인적인 용도로 제작되었습니다. 다른 사람이 사용하기 위해서는 코드 수정이 필요할 수 있습니다.
- **텔레그램 필요**: 봇의 작동을 확인하기 위해서는 텔레그램이 필요합니다.
- **LMS 기능**: 현재 LMS 기능은 테스트되지 않았습니다. 다른 요인으로 인해 정상적으로 작동하지 않을 수 있습니다.

## Supabase 스키마
봇은 Supabase 데이터베이스를 사용하여 다음과 같은 테이블에 데이터를 저장합니다.

- **notices**: 크롤링한 공지사항의 원문과 AI가 생성한 요약을 저장합니다.
- **crawling_logs**: 각 웹사이트의 마지막 크롤링 상태를 저장하여, 새로운 게시물만 식별하는 데 사용됩니다.
- **token_usage**: Gemini API 사용량을 추적합니다.
- **lms_assignments**: LMS에서 가져온 과제 정보를 저장합니다.
- **lms_notices**: LMS 공지사항을 저장합니다.
- **calendar_events**: 학사일정 정보를 저장합니다.
