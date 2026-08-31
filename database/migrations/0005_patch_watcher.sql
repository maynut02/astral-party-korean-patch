CREATE TABLE patch_watch_config (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    game_version text NOT NULL CHECK (btrim(game_version) <> ''),
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO patch_watch_config (singleton, game_version)
VALUES (true, '3.2.0');

CREATE TABLE patch_watch_routes (
    route text PRIMARY KEY CHECK (route IN ('INT_STEAM', 'CN_STEAM', 'INT_ANDROID')),
    game_version text,
    revision text,
    catalog_hash text CHECK (
        catalog_hash IS NULL OR catalog_hash ~ '^[0-9a-f]{32}$'
    ),
    source_url text,
    observed_at timestamptz,
    last_dispatched_game_version text,
    last_dispatched_revision text,
    last_dispatched_catalog_hash text CHECK (
        last_dispatched_catalog_hash IS NULL
        OR last_dispatched_catalog_hash ~ '^[0-9a-f]{32}$'
    ),
    dispatched_at timestamptz,
    CONSTRAINT patch_watch_routes_observed_shape CHECK (
        (game_version IS NULL AND revision IS NULL AND catalog_hash IS NULL AND observed_at IS NULL)
        OR
        (game_version IS NOT NULL AND revision IS NOT NULL AND catalog_hash IS NOT NULL AND observed_at IS NOT NULL)
    ),
    CONSTRAINT patch_watch_routes_dispatched_shape CHECK (
        (
            last_dispatched_game_version IS NULL
            AND last_dispatched_revision IS NULL
            AND last_dispatched_catalog_hash IS NULL
            AND dispatched_at IS NULL
        )
        OR
        (
            last_dispatched_game_version IS NOT NULL
            AND last_dispatched_revision IS NOT NULL
            AND last_dispatched_catalog_hash IS NOT NULL
            AND dispatched_at IS NOT NULL
        )
    )
);

INSERT INTO patch_watch_routes (route)
VALUES ('INT_STEAM'), ('CN_STEAM'), ('INT_ANDROID');
