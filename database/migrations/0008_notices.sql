CREATE TABLE notices (
    id uuid PRIMARY KEY,
    title text NOT NULL
        CHECK (btrim(title) <> '' AND char_length(title) <= 160),
    body text NOT NULL
        CHECK (btrim(body) <> '' AND char_length(body) <= 10000),
    created_by text NOT NULL REFERENCES app_users(google_sub) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX notices_created_idx
    ON notices (created_at DESC, id DESC);
