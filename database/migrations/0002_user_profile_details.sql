ALTER TABLE app_users
    ADD COLUMN game_uid text,
    ADD COLUMN game_invite_code text;

ALTER TABLE app_users
    ADD CONSTRAINT app_users_game_uid_length_check
        CHECK (game_uid IS NULL OR char_length(game_uid) <= 64),
    ADD CONSTRAINT app_users_game_invite_code_length_check
        CHECK (game_invite_code IS NULL OR char_length(game_invite_code) <= 64);

CREATE TABLE app_user_social_links (
    id uuid PRIMARY KEY,
    user_id text NOT NULL REFERENCES app_users(google_sub) ON DELETE CASCADE,
    label text NOT NULL CHECK (btrim(label) <> '' AND char_length(label) <= 30),
    url text NOT NULL CHECK (btrim(url) <> '' AND char_length(url) <= 500 AND url ~* '^https?://'),
    sort_order integer NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX app_user_social_links_user_order_idx
    ON app_user_social_links (user_id, sort_order, created_at);
