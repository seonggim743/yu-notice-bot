-- Durable per-channel notice delivery outbox.
-- Apply manually through the Supabase SQL editor before deploying code that
-- calls persist_notice_with_deliveries(). Existing notice RPCs remain intact.

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    notice_id UUID NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    site_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('telegram', 'discord')),
    event_type TEXT NOT NULL CHECK (event_type IN ('new', 'modified')),
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'superseded')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    alerted_at TIMESTAMPTZ,
    external_message_id JSONB,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (notice_id, content_hash, channel)
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_due
ON notification_deliveries(next_attempt_at)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_site
ON notification_deliveries(site_key, status);

-- The outbox contains serialized notice payloads and is an internal service
-- implementation detail. RLS provides defense in depth even if table grants
-- are changed later; the service role bypasses RLS.
ALTER TABLE notification_deliveries ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE notification_deliveries FROM anon, authenticated;
GRANT ALL ON TABLE notification_deliveries TO service_role;

CREATE OR REPLACE FUNCTION persist_notice_with_deliveries(
    p_notice JSONB,
    p_attachments JSONB[],
    p_deliveries JSONB[]
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_notice_id UUID;
    v_delivery JSONB;
    v_content_hash TEXT := p_notice->>'content_hash';
BEGIN
    -- Nested function calls participate in this transaction, so notice,
    -- attachments and delivery rows commit or roll back together.
    SELECT upsert_notice_with_attachments(p_notice, p_attachments)
    INTO v_notice_id;

    UPDATE notification_deliveries
    SET status = 'superseded', updated_at = NOW()
    WHERE notice_id = v_notice_id
      AND status = 'pending'
      AND content_hash <> v_content_hash;

    IF array_length(p_deliveries, 1) > 0 THEN
        FOREACH v_delivery IN ARRAY p_deliveries
        LOOP
            INSERT INTO notification_deliveries (
                notice_id,
                site_key,
                content_hash,
                channel,
                event_type,
                payload
            ) VALUES (
                v_notice_id,
                p_notice->>'site_key',
                v_content_hash,
                v_delivery->>'channel',
                v_delivery->>'event_type',
                v_delivery->'payload'
            )
            ON CONFLICT (notice_id, content_hash, channel) DO UPDATE SET
                payload = EXCLUDED.payload,
                event_type = EXCLUDED.event_type,
                status = CASE
                    WHEN notification_deliveries.status = 'superseded'
                    THEN 'pending'
                    ELSE notification_deliveries.status
                END,
                attempt_count = CASE
                    WHEN notification_deliveries.status = 'superseded'
                    THEN 0
                    ELSE notification_deliveries.attempt_count
                END,
                next_attempt_at = CASE
                    WHEN notification_deliveries.status = 'superseded'
                    THEN NOW()
                    ELSE notification_deliveries.next_attempt_at
                END,
                last_error = CASE
                    WHEN notification_deliveries.status = 'superseded'
                    THEN NULL
                    ELSE notification_deliveries.last_error
                END,
                alerted_at = CASE
                    WHEN notification_deliveries.status = 'superseded'
                    THEN NULL
                    ELSE notification_deliveries.alerted_at
                END,
                updated_at = NOW()
            WHERE notification_deliveries.status <> 'sent';
        END LOOP;
    END IF;

    RETURN v_notice_id;
END;
$$;

CREATE OR REPLACE FUNCTION complete_notification_delivery(
    p_delivery_id UUID,
    p_external_message_id JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_notice_id UUID;
    v_channel TEXT;
BEGIN
    SELECT notice_id, channel
    INTO v_notice_id, v_channel
    FROM notification_deliveries
    WHERE id = p_delivery_id
      AND status = 'pending'
    FOR UPDATE;

    IF v_notice_id IS NULL THEN
        RETURN FALSE;
    END IF;

    UPDATE notification_deliveries
    SET status = 'sent',
        external_message_id = p_external_message_id,
        sent_at = NOW(),
        updated_at = NOW(),
        last_error = NULL
    WHERE id = p_delivery_id;

    IF v_channel = 'telegram' THEN
        UPDATE notices
        SET message_ids = jsonb_set(
                COALESCE(message_ids, '{}'::JSONB),
                '{telegram}',
                p_external_message_id,
                TRUE
            ),
            updated_at = NOW()
        WHERE id = v_notice_id;
    ELSIF v_channel = 'discord' THEN
        UPDATE notices
        SET discord_thread_id = p_external_message_id #>> '{}',
            updated_at = NOW()
        WHERE id = v_notice_id;
    END IF;

    RETURN TRUE;
END;
$$;

COMMENT ON TABLE notification_deliveries IS
'Durable per-channel notification outbox. Pending rows retry until sent or superseded by a newer notice version.';

-- PostgreSQL grants function execution to PUBLIC by default. Only the backend
-- service role should be able to persist notices or mutate delivery state.
REVOKE EXECUTE ON FUNCTION persist_notice_with_deliveries(JSONB, JSONB[], JSONB[])
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION persist_notice_with_deliveries(JSONB, JSONB[], JSONB[])
TO service_role;

REVOKE EXECUTE ON FUNCTION complete_notification_delivery(UUID, JSONB)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION complete_notification_delivery(UUID, JSONB)
TO service_role;
