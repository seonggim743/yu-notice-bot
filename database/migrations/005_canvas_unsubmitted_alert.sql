-- Track one-time alerts for Canvas assignments that pass the due date
-- without a submission.

ALTER TABLE canvas_items
ADD COLUMN IF NOT EXISTS alerted_unsubmitted BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_canvas_items_unsubmitted_alert
ON canvas_items(due_at)
WHERE item_type = 'assignment'
  AND has_submitted = FALSE
  AND alerted_unsubmitted = FALSE
  AND due_at IS NOT NULL;
