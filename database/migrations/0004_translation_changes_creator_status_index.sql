CREATE INDEX translation_changes_creator_status_idx
    ON translation_changes (created_by, status);
