-- Migration: Create upsert_notice_with_attachments RPC function
-- Description: Ensures atomicity when updating notices and their attachments.

CREATE OR REPLACE FUNCTION upsert_notice_with_attachments(
    p_notice JSONB,
    p_attachments JSONB[]
)
RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_notice_id UUID;
    v_attachment JSONB;
BEGIN
    -- 1. Upsert Notice
    INSERT INTO notices (
        site_key, article_id, title, url, content, category,
        published_at, author, content_hash, summary, embedding,
        image_urls, attachment_text, message_ids, discord_thread_id,
        deadline, eligibility, start_date, end_date, target_dept, target_grades, tags,
        updated_at
    ) VALUES (
        p_notice->>'site_key',
        p_notice->>'article_id',
        p_notice->>'title',
        p_notice->>'url',
        p_notice->>'content',
        p_notice->>'category',
        (p_notice->>'published_at')::TIMESTAMPTZ,
        p_notice->>'author',
        p_notice->>'content_hash',
        p_notice->>'summary',
        (p_notice->>'embedding')::VECTOR,
        (p_notice->>'image_urls')::TEXT[],
        p_notice->>'attachment_text',
        COALESCE((p_notice->>'message_ids')::JSONB, '{}'::JSONB),
        p_notice->>'discord_thread_id',
        (p_notice->>'deadline')::DATE,
        (p_notice->>'eligibility')::TEXT[],
        (p_notice->>'start_date')::DATE,
        (p_notice->>'end_date')::DATE,
        p_notice->>'target_dept',
        (p_notice->>'target_grades')::INTEGER[],
        (p_notice->>'tags')::TEXT[],
        NOW()
    )
    ON CONFLICT (site_key, article_id) DO UPDATE SET
        title = EXCLUDED.title,
        url = EXCLUDED.url,
        content = EXCLUDED.content,
        category = EXCLUDED.category,
        published_at = EXCLUDED.published_at,
        author = EXCLUDED.author,
        content_hash = EXCLUDED.content_hash,
        summary = EXCLUDED.summary,
        embedding = EXCLUDED.embedding,
        image_urls = EXCLUDED.image_urls,
        attachment_text = EXCLUDED.attachment_text,
        message_ids = EXCLUDED.message_ids,
        discord_thread_id = EXCLUDED.discord_thread_id,
        deadline = EXCLUDED.deadline,
        eligibility = EXCLUDED.eligibility,
        start_date = EXCLUDED.start_date,
        end_date = EXCLUDED.end_date,
        target_dept = EXCLUDED.target_dept,
        target_grades = EXCLUDED.target_grades,
        tags = EXCLUDED.tags,
        updated_at = NOW()
    RETURNING id INTO v_notice_id;

    -- 2. Delete existing attachments
    DELETE FROM attachments WHERE notice_id = v_notice_id;

    -- 3. Insert new attachments
    IF array_length(p_attachments, 1) > 0 THEN
        FOREACH v_attachment IN ARRAY p_attachments
        LOOP
            INSERT INTO attachments (
                notice_id, name, url, file_size, etag
            ) VALUES (
                v_notice_id,
                v_attachment->>'name',
                v_attachment->>'url',
                (v_attachment->>'file_size')::BIGINT,
                v_attachment->>'etag'
            );
        END LOOP;
    END IF;

    RETURN v_notice_id;
END;
$$;
