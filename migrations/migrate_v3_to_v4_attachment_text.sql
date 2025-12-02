-- Add tags column and enhanced metadata fields
-- This migration adds support for AI-selected Discord tags and enhanced metadata

-- Add tags column (AI-selected tag names for Discord)
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';

-- Add Tier 2 enhanced metadata fields
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS deadline DATE,
ADD COLUMN IF NOT EXISTS eligibility TEXT[] DEFAULT '{}';

-- Add comment for clarity
COMMENT ON COLUMN notices.tags IS 'AI-selected tag names (1-5) for Discord forum tags';
COMMENT ON COLUMN notices.deadline IS 'Deadline for application/submission (YYYY-MM-DD)';
COMMENT ON COLUMN notices.eligibility IS 'Eligibility requirements or conditions';
