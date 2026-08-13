ALTER TABLE translations
    DROP CONSTRAINT IF EXISTS translations_unit_locale_key;

ALTER TABLE translations
    ADD CONSTRAINT translations_unit_locale_fingerprint_key
    UNIQUE (unit_id, locale, source_fingerprint);

CREATE INDEX IF NOT EXISTS translations_unit_locale_updated_idx
    ON translations (unit_id, locale, updated_at DESC);
