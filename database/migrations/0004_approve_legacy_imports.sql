WITH untouched_legacy_translations AS (
    SELECT t.id
    FROM translations AS t
    WHERE t.status = 'draft'
      AND EXISTS (
          SELECT 1
          FROM translation_history AS h
          WHERE h.translation_id = t.id
            AND h.actor = 'legacy-import'
      )
      AND NOT EXISTS (
          SELECT 1
          FROM translation_history AS h
          WHERE h.translation_id = t.id
            AND h.actor IS DISTINCT FROM 'legacy-import'
      )
)
UPDATE translations AS t
SET status = 'approved',
    updated_at = now(),
    updated_by = 'legacy-import-approval-migration'
FROM untouched_legacy_translations AS legacy
WHERE t.id = legacy.id;
