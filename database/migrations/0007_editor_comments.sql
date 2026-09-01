CREATE TABLE editor_comments (
    id uuid PRIMARY KEY,
    unit_id uuid NOT NULL REFERENCES translation_units(id) ON DELETE CASCADE,
    created_by text NOT NULL REFERENCES app_users(google_sub) ON DELETE RESTRICT,
    body text NOT NULL
        CHECK (btrim(body) <> '' AND char_length(body) <= 2000),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX editor_comments_unit_created_idx
    ON editor_comments (unit_id, created_at DESC, id DESC);

CREATE INDEX editor_comments_created_idx
    ON editor_comments (created_at DESC, id DESC);
