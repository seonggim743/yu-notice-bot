-- Add discord_thread_id to track Forum Threads for updates
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS discord_thread_id TEXT;
