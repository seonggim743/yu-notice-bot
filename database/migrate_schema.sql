-- Run this in Supabase SQL Editor to fix the schema error
ALTER TABLE notices ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS target_grades INTEGER[];
ALTER TABLE notices ADD COLUMN IF NOT EXISTS target_dept TEXT;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS start_date DATE;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS end_date DATE;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS change_details JSONB DEFAULT '{}'::jsonb;
ALTER TABLE notices ADD COLUMN IF NOT EXISTS message_ids JSONB DEFAULT '{}'::jsonb;

-- Reload schema cache (PostgREST)
NOTIFY pgrst, 'reload config';
