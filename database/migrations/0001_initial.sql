CREATE TABLE game_revisions (
    id uuid PRIMARY KEY,
    route text NOT NULL,
    game_version text NOT NULL,
    revision text NOT NULL,
    source_url text NOT NULL,
    catalog_url text NOT NULL,
    catalog_sha256 text NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
    catalog_build_hash text,
    detected_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT game_revisions_route_version_revision_key
        UNIQUE (route, game_version, revision)
);

CREATE INDEX game_revisions_route_detected_idx
    ON game_revisions (route, detected_at DESC);

CREATE TABLE asset_locations (
    id uuid PRIMARY KEY,
    revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE CASCADE,
    logical_name text NOT NULL,
    catalog_key text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('remote', 'runtime', 'game_data')),
    bundle_name text,
    bundle_hash text,
    cache_root text,
    asset_type text NOT NULL,
    asset_name text NOT NULL,
    source_sha256 text CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'),
    source_size bigint CHECK (source_size IS NULL OR source_size >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT asset_locations_revision_logical_asset_key
        UNIQUE (revision_id, logical_name, asset_type, asset_name)
);

CREATE INDEX asset_locations_revision_idx ON asset_locations (revision_id);

CREATE TABLE translation_units (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('lang', 'str')),
    namespace text NOT NULL CHECK (btrim(namespace) <> ''),
    unit_key text NOT NULL CHECK (btrim(unit_key) <> ''),
    current_source_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT translation_units_identity_key UNIQUE (kind, namespace, unit_key)
);

CREATE TABLE source_versions (
    id uuid PRIMARY KEY,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    cn_s text NOT NULL DEFAULT '',
    en text NOT NULL DEFAULT '',
    jp text NOT NULL DEFAULT '',
    cn_t text NOT NULL DEFAULT '',
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    first_seen_revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT source_versions_unit_fingerprint_key UNIQUE (unit_id, source_fingerprint)
);

ALTER TABLE translation_units
    ADD CONSTRAINT translation_units_current_source_version_fk
    FOREIGN KEY (current_source_version_id)
    REFERENCES source_versions(id)
    ON DELETE SET NULL;

CREATE INDEX source_versions_unit_created_idx
    ON source_versions (unit_id, created_at DESC);
CREATE INDEX source_versions_fingerprint_idx
    ON source_versions (source_fingerprint);

-- One INT_STEAM game revision is the source-change group. Only added/modified/removed
-- units get rows here; unchanged source text is not duplicated per revision.
CREATE TABLE source_changes (
    revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE CASCADE,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE RESTRICT,
    change_type text NOT NULL CHECK (change_type IN ('added', 'modified', 'removed')),
    old_source_version_id uuid REFERENCES source_versions(id) ON DELETE RESTRICT,
    new_source_version_id uuid REFERENCES source_versions(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'applied' CHECK (status = 'applied'),
    applied_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, unit_id),
    CONSTRAINT source_changes_shape_check CHECK (
        (change_type = 'added' AND old_source_version_id IS NULL AND new_source_version_id IS NOT NULL)
        OR
        (change_type = 'modified' AND old_source_version_id IS NOT NULL AND new_source_version_id IS NOT NULL
            AND old_source_version_id <> new_source_version_id)
        OR
        (change_type = 'removed' AND old_source_version_id IS NOT NULL AND new_source_version_id IS NULL)
    )
);

CREATE INDEX source_changes_unit_revision_idx
    ON source_changes (unit_id, revision_id);

CREATE TABLE translation_change_groups (
    id uuid PRIMARY KEY,
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Translation edits live here first. A pending/rejected/superseded proposal never changes
-- the production translation used by patch builds.
CREATE TABLE translation_changes (
    id uuid PRIMARY KEY,
    group_id uuid NOT NULL REFERENCES translation_change_groups(id) ON DELETE RESTRICT,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    locale text NOT NULL CHECK (btrim(locale) <> ''),
    source_version_id uuid NOT NULL REFERENCES source_versions(id) ON DELETE RESTRICT,
    previous_text text,
    proposed_text text NOT NULL CHECK (btrim(proposed_text) <> ''),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_by text,
    reviewed_at timestamptz,
    CONSTRAINT translation_changes_review_check CHECK (
        (status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR
        (status <> 'pending' AND reviewed_at IS NOT NULL)
    ),
    CONSTRAINT translation_changes_group_unit_locale_key UNIQUE (group_id, unit_id, locale)
);

CREATE INDEX translation_changes_unit_locale_created_idx
    ON translation_changes (unit_id, locale, created_at DESC);
CREATE INDEX translation_changes_status_created_idx
    ON translation_changes (status, created_at DESC);
CREATE INDEX translation_changes_group_idx
    ON translation_changes (group_id, created_at);

CREATE FUNCTION enforce_translation_change_source()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_source uuid;
BEGIN
    SELECT current_source_version_id INTO current_source
    FROM translation_units
    WHERE id = NEW.unit_id;

    IF NOT EXISTS (
        SELECT 1 FROM source_versions
        WHERE id = NEW.source_version_id AND unit_id = NEW.unit_id
    ) THEN
        RAISE EXCEPTION 'translation change source does not belong to unit';
    END IF;
    IF TG_OP = 'INSERT' AND current_source IS DISTINCT FROM NEW.source_version_id THEN
        RAISE EXCEPTION 'new translation change must target the current source version';
    END IF;
    IF NEW.status = 'approved' AND current_source IS DISTINCT FROM NEW.source_version_id THEN
        RAISE EXCEPTION 'cannot approve translation change for a stale source version';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER translation_changes_require_valid_source
BEFORE INSERT OR UPDATE OF unit_id, source_version_id, status ON translation_changes
FOR EACH ROW
EXECUTE FUNCTION enforce_translation_change_source();

-- This is the production translation table. There is no status column: every row in this
-- table is approved and may be consumed by a patch without any additional state checks.
CREATE TABLE translations (
    id uuid PRIMARY KEY,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    locale text NOT NULL CHECK (btrim(locale) <> ''),
    text text NOT NULL CHECK (btrim(text) <> ''),
    approved_source_version_id uuid NOT NULL REFERENCES source_versions(id) ON DELETE RESTRICT,
    applied_change_id uuid NOT NULL UNIQUE REFERENCES translation_changes(id) ON DELETE RESTRICT,
    approved_by text,
    approved_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT translations_unit_locale_key UNIQUE (unit_id, locale)
);

CREATE INDEX translations_locale_updated_idx
    ON translations (locale, updated_at DESC);

-- Enforce the main invariant at the database boundary as well as in application code:
-- production translations can only point at an approved proposal for the same unit/locale/text.
CREATE FUNCTION enforce_approved_translation_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    change_row translation_changes%ROWTYPE;
BEGIN
    SELECT * INTO change_row
    FROM translation_changes
    WHERE id = NEW.applied_change_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'approved translation change not found: %', NEW.applied_change_id;
    END IF;
    IF change_row.status <> 'approved' THEN
        RAISE EXCEPTION 'translation change % is not approved', NEW.applied_change_id;
    END IF;
    IF change_row.unit_id <> NEW.unit_id OR change_row.locale <> NEW.locale THEN
        RAISE EXCEPTION 'translation change identity does not match production translation';
    END IF;
    IF change_row.proposed_text <> NEW.text THEN
        RAISE EXCEPTION 'translation change text does not match production translation';
    END IF;
    IF change_row.source_version_id <> NEW.approved_source_version_id THEN
        RAISE EXCEPTION 'translation change source version does not match production translation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER translations_require_approved_change
BEFORE INSERT OR UPDATE ON translations
FOR EACH ROW
EXECUTE FUNCTION enforce_approved_translation_change();

CREATE TABLE builds (
    id uuid PRIMARY KEY,
    revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE RESTRICT,
    route text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('release', 'develop')),
    translation_fingerprint text NOT NULL CHECK (translation_fingerprint ~ '^[0-9a-f]{64}$'),
    git_commit text,
    github_run_id text,
    status text NOT NULL CHECK (status IN ('building', 'validated', 'released', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz
);

CREATE INDEX builds_revision_channel_created_idx
    ON builds (revision_id, channel, created_at DESC);

CREATE TABLE build_files (
    build_id uuid NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    target text NOT NULL CHECK (target IN ('addressables', 'game_data')),
    relative_path text NOT NULL,
    operation text NOT NULL DEFAULT 'replace' CHECK (operation IN ('create', 'replace')),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size bigint NOT NULL CHECK (size >= 0),
    PRIMARY KEY (build_id, target, relative_path)
);

-- Web/editor-facing projections. These deliberately expose the current source and production
-- translation without requiring consumers to replay source history or understand patch rules.
CREATE VIEW current_source_texts AS
SELECT
    tu.id AS unit_id,
    tu.kind,
    tu.namespace,
    tu.unit_key,
    sv.id AS source_version_id,
    sv.cn_s,
    sv.en,
    sv.jp,
    sv.cn_t,
    sv.source_fingerprint,
    sv.first_seen_revision_id,
    sv.created_at AS source_created_at
FROM translation_units tu
JOIN source_versions sv ON sv.id = tu.current_source_version_id;

CREATE VIEW translation_workbench AS
SELECT
    src.unit_id,
    src.kind,
    src.namespace,
    src.unit_key,
    src.source_version_id,
    src.cn_s,
    src.en,
    src.jp,
    src.cn_t,
    src.source_fingerprint,
    tr.id AS translation_id,
    tr.text AS approved_text,
    tr.approved_source_version_id,
    (tr.approved_source_version_id = src.source_version_id) AS approved_for_current_source,
    tr.approved_by,
    tr.approved_at,
    latest_change.id AS latest_change_id,
    latest_change.status AS latest_change_status,
    latest_change.source_version_id AS latest_change_source_version_id,
    latest_change.proposed_text AS latest_proposed_text,
    latest_change.created_by AS latest_change_created_by,
    latest_change.created_at AS latest_change_created_at
FROM current_source_texts src
LEFT JOIN translations tr
    ON tr.unit_id = src.unit_id AND tr.locale = 'ko'
LEFT JOIN LATERAL (
    SELECT tc.id, tc.status, tc.source_version_id, tc.proposed_text, tc.created_by, tc.created_at
    FROM translation_changes tc
    WHERE tc.unit_id = src.unit_id AND tc.locale = 'ko'
    ORDER BY tc.created_at DESC, tc.id DESC
    LIMIT 1
) latest_change ON TRUE;

CREATE VIEW source_revision_change_summary AS
SELECT
    gr.id AS revision_id,
    gr.game_version,
    gr.revision,
    gr.detected_at,
    gr.processed_at,
    count(sc.unit_id)::bigint AS total_changes,
    count(sc.unit_id) FILTER (WHERE sc.change_type = 'added')::bigint AS added_count,
    count(sc.unit_id) FILTER (WHERE sc.change_type = 'modified')::bigint AS modified_count,
    count(sc.unit_id) FILTER (WHERE sc.change_type = 'removed')::bigint AS removed_count
FROM game_revisions gr
LEFT JOIN source_changes sc ON sc.revision_id = gr.id
WHERE gr.route = 'INT_STEAM'
GROUP BY gr.id, gr.game_version, gr.revision, gr.detected_at, gr.processed_at;

CREATE VIEW translation_change_group_summary AS
SELECT
    tg.id AS group_id,
    tg.title,
    tg.description,
    tg.created_by,
    tg.created_at,
    count(tc.id)::bigint AS total_changes,
    count(tc.id) FILTER (WHERE tc.status = 'pending')::bigint AS pending_count,
    count(tc.id) FILTER (WHERE tc.status = 'approved')::bigint AS approved_count,
    count(tc.id) FILTER (WHERE tc.status = 'rejected')::bigint AS rejected_count,
    count(tc.id) FILTER (WHERE tc.status = 'superseded')::bigint AS superseded_count
FROM translation_change_groups tg
LEFT JOIN translation_changes tc ON tc.group_id = tg.id
GROUP BY tg.id, tg.title, tg.description, tg.created_by, tg.created_at;

