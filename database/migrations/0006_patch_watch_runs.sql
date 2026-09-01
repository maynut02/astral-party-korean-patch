CREATE TABLE patch_watch_runs (
    id bigserial PRIMARY KEY,
    game_version text,
    status text NOT NULL CHECK (
        status IN (
            'running',
            'success',
            'partial_failure',
            'failed',
            'disabled',
            'skipped_locked'
        )
    ),
    workflow_dispatched boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    duration_ms bigint CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_message text,
    CONSTRAINT patch_watch_runs_finished_shape CHECK (
        (status = 'running' AND finished_at IS NULL AND duration_ms IS NULL)
        OR
        (status <> 'running' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    )
);

CREATE TABLE patch_watch_run_routes (
    run_id bigint NOT NULL REFERENCES patch_watch_runs(id) ON DELETE CASCADE,
    route text NOT NULL CHECK (route IN ('INT_STEAM', 'CN_STEAM', 'INT_ANDROID')),
    status text NOT NULL CHECK (
        status IN (
            'checking',
            'unchanged',
            'change_detected',
            'waiting_processing',
            'dispatched',
            'failed'
        )
    ),
    game_version text,
    revision text,
    catalog_hash text CHECK (
        catalog_hash IS NULL OR catalog_hash ~ '^[0-9a-f]{32}$'
    ),
    source_url text,
    changed boolean,
    dispatch_due boolean,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    duration_ms bigint CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_message text,
    PRIMARY KEY (run_id, route),
    CONSTRAINT patch_watch_run_routes_finished_shape CHECK (
        (
            status = 'checking'
            AND finished_at IS NULL
            AND duration_ms IS NULL
            AND changed IS NULL
            AND dispatch_due IS NULL
        )
        OR
        (
            status <> 'checking'
            AND finished_at IS NOT NULL
            AND duration_ms IS NOT NULL
            AND changed IS NOT NULL
            AND dispatch_due IS NOT NULL
        )
    )
);

CREATE INDEX patch_watch_runs_started_idx
    ON patch_watch_runs (started_at DESC);

CREATE INDEX patch_watch_run_routes_route_started_idx
    ON patch_watch_run_routes (route, started_at DESC);
