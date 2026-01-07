-- Add new columns for AI Enhanced Analysis (Tier 2)
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS deadline DATE,
ADD COLUMN IF NOT EXISTS eligibility TEXT[], -- Array of strings
ADD COLUMN IF NOT EXISTS start_date DATE,
ADD COLUMN IF NOT EXISTS end_date DATE,
ADD COLUMN IF NOT EXISTS target_grades INTEGER[], -- Array of integers
ADD COLUMN IF NOT EXISTS target_dept TEXT;

-- Notify PostgREST to reload schema (Supabase usually does this automatically, but good to know)
NOTIFY pgrst, 'reload config';
