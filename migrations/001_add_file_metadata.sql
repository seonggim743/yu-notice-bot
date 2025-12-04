-- Add file_size and etag columns to attachments table
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS file_size BIGINT;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS etag TEXT;
