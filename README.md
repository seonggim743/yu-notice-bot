# 🤖 YU Notice Bot (Cloud & Local)

영남대학교 공지사항 및 LMS 과제 알림 봇입니다.

## 🚀 기능
1.  **공지사항 알림**: 학교 홈페이지, 학사 공지, 기숙사 식단 등을 크롤링하여 텔레그램으로 전송합니다.
2.  **LMS 과제 알림**: Canvas LMS에서 미제출 과제와 새로운 공지사항을 확인하여 알림을 보냅니다.

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