-- Migration: Canvas LMS items (assignments, announcements, submissions)
--
-- One row per (canvas_id, item_type). Stores enough metadata to detect
-- modifications (canvas_updated_at, content_hash) and re-target the
-- previously-sent notification (message_ids, discord_thread_id) on edit.
--
-- Reminder bookkeeping (`reminders_sent`) is a JSONB array of integer
-- hours-before-deadline that have already been notified, so the daily
-- deadline pass doesn't re-spam.

CREATE TABLE IF NOT EXISTS canvas_items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    canvas_id BIGINT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('assignment', 'announcement', 'submission')),

    course_id BIGINT NOT NULL,
    course_name TEXT,

    title TEXT NOT NULL,
    body TEXT,
    content_hash TEXT,

    -- assignment-specific
    due_at TIMESTAMPTZ,
    points_possible NUMERIC,
    submission_types TEXT[],
    has_submitted BOOLEAN DEFAULT FALSE,

    -- submission-specific
    assignment_canvas_id BIGINT,
    score NUMERIC,
    grade TEXT,
    workflow_state TEXT,

    -- shared
    html_url TEXT,
    canvas_created_at TIMESTAMPTZ,
    canvas_updated_at TIMESTAMPTZ,

    -- notification tracking (parallel to notices.message_ids / discord_thread_id)
    message_ids JSONB DEFAULT '{}'::jsonb,
    discord_thread_id TEXT,
    reminders_sent JSONB DEFAULT '[]'::jsonb,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(canvas_id, item_type)
);

CREATE INDEX IF NOT EXISTS idx_canvas_items_course ON canvas_items(course_id);
CREATE INDEX IF NOT EXISTS idx_canvas_items_due_at ON canvas_items(due_at) WHERE due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_items_type ON canvas_items(item_type);

COMMENT ON TABLE canvas_items IS 'Canvas LMS items (assignments, announcements, submissions) tracked for change detection and notification.';

-- =====================================================
-- upsert_canvas_item RPC
--
-- Same pattern as upsert_notice_with_attachments: preserve message_ids
-- when the incoming payload has '{}' (caller didn't track ids yet),
-- preserve discord_thread_id on NULL, and preserve reminders_sent on
-- empty array. Returns the row id and whether it was inserted (vs. updated).
-- =====================================================
CREATE OR REPLACE FUNCTION upsert_canvas_item(p_item JSONB)
RETURNS TABLE(item_id UUID, was_inserted BOOLEAN)
LANGUAGE plpgsql
AS $$
DECLARE
    v_id UUID;
    v_inserted BOOLEAN;
BEGIN
    INSERT INTO canvas_items (
        canvas_id, item_type, course_id, course_name,
        title, body, content_hash,
        due_at, points_possible, submission_types, has_submitted,
        assignment_canvas_id, score, grade, workflow_state,
        html_url, canvas_created_at, canvas_updated_at,
        message_ids, discord_thread_id, reminders_sent,
        updated_at
    ) VALUES (
        (p_item->>'canvas_id')::BIGINT,
        p_item->>'item_type',
        (p_item->>'course_id')::BIGINT,
        p_item->>'course_name',
        p_item->>'title',
        p_item->>'body',
        p_item->>'content_hash',
        (p_item->>'due_at')::TIMESTAMPTZ,
        (p_item->>'points_possible')::NUMERIC,
        CASE
            WHEN p_item->'submission_types' IS NULL OR jsonb_typeof(p_item->'submission_types') = 'null' THEN ARRAY[]::TEXT[]
            ELSE ARRAY(SELECT jsonb_array_elements_text(p_item->'submission_types'))
        END,
        COALESCE((p_item->>'has_submitted')::BOOLEAN, FALSE),
        (p_item->>'assignment_canvas_id')::BIGINT,
        (p_item->>'score')::NUMERIC,
        p_item->>'grade',
        p_item->>'workflow_state',
        p_item->>'html_url',
        (p_item->>'canvas_created_at')::TIMESTAMPTZ,
        (p_item->>'canvas_updated_at')::TIMESTAMPTZ,
        COALESCE((p_item->>'message_ids')::JSONB, '{}'::jsonb),
        p_item->>'discord_thread_id',
        COALESCE((p_item->>'reminders_sent')::JSONB, '[]'::jsonb),
        NOW()
    )
    ON CONFLICT (canvas_id, item_type) DO UPDATE SET
        course_id = EXCLUDED.course_id,
        course_name = EXCLUDED.course_name,
        title = EXCLUDED.title,
        body = EXCLUDED.body,
        content_hash = EXCLUDED.content_hash,
        due_at = EXCLUDED.due_at,
        points_possible = EXCLUDED.points_possible,
        submission_types = EXCLUDED.submission_types,
        has_submitted = EXCLUDED.has_submitted,
        assignment_canvas_id = EXCLUDED.assignment_canvas_id,
        score = EXCLUDED.score,
        grade = EXCLUDED.grade,
        workflow_state = EXCLUDED.workflow_state,
        html_url = EXCLUDED.html_url,
        canvas_created_at = EXCLUDED.canvas_created_at,
        canvas_updated_at = EXCLUDED.canvas_updated_at,
        message_ids = CASE
            WHEN EXCLUDED.message_ids = '{}'::jsonb THEN canvas_items.message_ids
            ELSE EXCLUDED.message_ids
        END,
        discord_thread_id = COALESCE(EXCLUDED.discord_thread_id, canvas_items.discord_thread_id),
        reminders_sent = CASE
            WHEN EXCLUDED.reminders_sent = '[]'::jsonb THEN canvas_items.reminders_sent
            ELSE EXCLUDED.reminders_sent
        END,
        updated_at = NOW()
    RETURNING id, (xmax = 0) INTO v_id, v_inserted;

    RETURN QUERY SELECT v_id, v_inserted;
END;
$$;
