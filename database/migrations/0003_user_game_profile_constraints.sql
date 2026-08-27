ALTER TABLE app_users
    ADD CONSTRAINT app_users_game_uid_format_check
        CHECK (game_uid IS NULL OR game_uid ~ '^[0-9]+$'),
    ADD CONSTRAINT app_users_game_invite_code_format_check
        CHECK (game_invite_code IS NULL OR game_invite_code ~ '^[A-Za-z0-9]+$');
