-- Migration v5: Change image_url to image_urls array
-- This migration adds support for multiple images in notices

-- Step 1: Add new image_urls column
ALTER TABLE notices 
ADD COLUMN IF NOT EXISTS image_urls TEXT[] DEFAULT '{}';

-- Step 2: Migrate existing data from image_url to image_urls
-- If image_url is not null, convert it to a single-element array
UPDATE notices 
SET image_urls = ARRAY[image_url]
WHERE image_url IS NOT NULL AND image_url != '';

-- Step 3: Drop old image_url column (optional - comment out if you want to keep it for rollback)
-- ALTER TABLE notices DROP COLUMN IF EXISTS image_url;

-- Add comment for clarity
COMMENT ON COLUMN notices.image_urls IS 'Array of image URLs found in notice content';
