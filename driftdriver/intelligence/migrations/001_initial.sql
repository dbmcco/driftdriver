-- ABOUTME: Initial Postgres schema for the ecosystem intelligence signal store
-- ABOUTME: Creates signals, evaluation_runs, and source_configs with the indexes required by the first task

CREATE TABLE IF NOT EXISTS signals (
    id uuid PRIMARY KEY,
    source_type text NOT NULL CHECK (length(btrim(source_type)) > 0),
    source_id text NOT NULL CHECK (length(btrim(source_id)) > 0),
    signal_type text NOT NULL CHECK (length(btrim(signal_type)) > 0),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    detected_at timestamptz NOT NULL,
    evaluated_at timestamptz,
    decision text,
    decision_reason text,
    decision_confidence double precision CHECK (
        decision_confidence IS NULL
        OR (decision_confidence >= 0.0 AND decision_confidence <= 1.0)
    ),
    decided_by text,
    acted_on boolean NOT NULL DEFAULT false,
    action_log jsonb NOT NULL DEFAULT '[]'::jsonb,
    vetoed_at timestamptz,
    veto_reason text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT signals_source_identity_key UNIQUE (source_type, source_id, signal_type)
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id uuid PRIMARY KEY,
    run_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_types text[] NOT NULL DEFAULT '{}'::text[],
    signals_created integer NOT NULL DEFAULT 0 CHECK (signals_created >= 0),
    signals_evaluated integer NOT NULL DEFAULT 0 CHECK (signals_evaluated >= 0),
    auto_decisions jsonb NOT NULL DEFAULT '{}'::jsonb,
    escalated integer NOT NULL DEFAULT 0 CHECK (escalated >= 0),
    llm_model text,
    llm_tokens_used integer NOT NULL DEFAULT 0 CHECK (llm_tokens_used >= 0),
    duration_ms integer NOT NULL DEFAULT 0 CHECK (duration_ms >= 0)
);

CREATE TABLE IF NOT EXISTS source_configs (
    id uuid PRIMARY KEY,
    source_type text NOT NULL UNIQUE CHECK (length(btrim(source_type)) > 0),
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    last_synced_at timestamptz,
    sync_interval_minutes integer NOT NULL DEFAULT 60 CHECK (sync_interval_minutes > 0)
);

CREATE INDEX IF NOT EXISTS idx_signals_source_type_evaluated_at
    ON signals (source_type, evaluated_at);

CREATE INDEX IF NOT EXISTS idx_signals_decision_acted_on
    ON signals (decision, acted_on);
