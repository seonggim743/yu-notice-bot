# Database Migrations

이 폴더는 **기존 데이터베이스를 업데이트**하는 사용자를 위한 마이그레이션 파일들입니다.

## 새로 설치하는 경우

**루트의 `schema.sql`을 사용하세요!** (Version 5 - 최신)

```bash
# Supabase SQL Editor에서 실행
psql -h your-db-host -d your-db -f schema.sql
```

## 기존 데이터베이스 업데이트

현재 버전을 확인하고 필요한 마이그레이션만 순차적으로 실행하세요:

### v1 → v2: Metadata Fields 추가
```sql
-- migrate_v1_to_v2.sql
-- Tags, eligibility, deadline 등 AI 분석 필드 추가
```

### v2 → v3: Author/Published Date
```sql
-- migrate_v2_to_v3.sql
-- author, published_at 필드 추가
```

### v3 → v4: Discord Thread ID
```sql
-- migrate_v3_to_v4.sql
-- discord_thread_id 필드 추가 (수정 공지 스레드 답글용)
```

### v4 → v5: Multiple Images
```sql
-- migrate_v4_to_v5.sql
-- image_url → image_urls 배열로 변경
```

## 마이그레이션 적용 방법

### 방법 1: 스크립트 사용
```bash
cd scripts
python apply_migration.py ../migrations/migrate_v4_to_v5.sql
```

### 방법 2: 직접 실행
```bash
psql -h your-db-host -d your-db -f migrations/migrate_v4_to_v5.sql
```

## 현재 버전 확인

```sql
-- notices 테이블 구조 확인
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'notices'
ORDER BY ordinal_position;

-- image_urls 컬럼이 있으면 v5
-- discord_thread_id가 있으면 v4 이상
-- tags가 있으면 v2 이상
```

## 버전 히스토리

- **v5** (2024-12): Multiple images support
- **v4** (2024-11): Discord thread replies
- **v3** (2024-11): Author metadata extraction
- **v2** (2024-11): AI metadata fields
- **v1** (2024-10): Initial schema
