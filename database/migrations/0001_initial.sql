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
    namespace text NOT NULL,
    unit_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT translation_units_identity_key UNIQUE (kind, namespace, unit_key)
);

CREATE TABLE source_texts (
    revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE CASCADE,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE RESTRICT,
    cn_s text NOT NULL DEFAULT '',
    en text NOT NULL DEFAULT '',
    jp text NOT NULL DEFAULT '',
    cn_t text NOT NULL DEFAULT '',
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, unit_id)
);

CREATE INDEX source_texts_unit_revision_idx ON source_texts (unit_id, revision_id);
CREATE INDEX source_texts_fingerprint_idx ON source_texts (source_fingerprint);

CREATE TABLE translations (
    id uuid PRIMARY KEY,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    locale text NOT NULL,
    text text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'approved')),
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    CONSTRAINT translations_unit_locale_key UNIQUE (unit_id, locale)
);

CREATE INDEX translations_locale_status_idx ON translations (locale, status);

CREATE TABLE translation_history (
    id uuid PRIMARY KEY,
    translation_id uuid NOT NULL REFERENCES translations(id) ON DELETE CASCADE,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    locale text NOT NULL,
    old_text text,
    new_text text NOT NULL,
    old_status text,
    new_status text NOT NULL,
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    actor text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX translation_history_unit_created_idx
    ON translation_history (unit_id, created_at DESC);

CREATE TABLE builds (
    id uuid PRIMARY KEY,
    revision_id uuid NOT NULL REFERENCES game_revisions(id) ON DELETE RESTRICT,
    route text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('preview', 'stable')),
    translation_fingerprint text NOT NULL CHECK (translation_fingerprint ~ '^[0-9a-f]{64}$'),
    git_commit text,
    github_run_id text,
    status text NOT NULL CHECK (status IN ('building', 'validated', 'released', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    released_at timestamptz,
    CONSTRAINT builds_unique_snapshot UNIQUE (
        revision_id,
        channel,
        translation_fingerprint
    )
);

CREATE TABLE build_files (
    build_id uuid NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    target text NOT NULL CHECK (target IN ('addressables', 'game_data')),
    relative_path text NOT NULL,
    operation text NOT NULL DEFAULT 'replace' CHECK (operation IN ('create', 'replace')),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size bigint NOT NULL CHECK (size >= 0),
    PRIMARY KEY (build_id, target, relative_path)
);

