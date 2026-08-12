ALTER TABLE builds
    DROP CONSTRAINT IF EXISTS builds_unique_snapshot;

CREATE INDEX IF NOT EXISTS builds_revision_channel_created_idx
    ON builds (revision_id, channel, created_at DESC);
