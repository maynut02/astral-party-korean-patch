ALTER TABLE builds
    DROP CONSTRAINT IF EXISTS builds_channel_check;

UPDATE builds
SET channel = 'develop'
WHERE channel IN ('preview', 'stable');

ALTER TABLE builds
    ADD CONSTRAINT builds_channel_check
    CHECK (channel IN ('release', 'develop'));
