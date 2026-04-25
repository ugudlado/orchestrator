-- Phase 4 of workflow-engine-as-state-machine.
-- Adds phase_events and driver_sessions tables for level-aware writes from `orchestrator done`.
-- timestamps stored UTC by convention.

CREATE TABLE IF NOT EXISTS phase_events (
  repo_root         VARCHAR NOT NULL,
  change_id         VARCHAR NOT NULL,
  phase             VARCHAR NOT NULL,
  attempt           INTEGER NOT NULL,
  step_count        INTEGER NOT NULL,
  cost_usd          DOUBLE  NOT NULL DEFAULT 0.0,
  input_tokens      BIGINT  NOT NULL DEFAULT 0,
  output_tokens     BIGINT  NOT NULL DEFAULT 0,
  cache_read_input_tokens     BIGINT NOT NULL DEFAULT 0,
  cache_creation_input_tokens BIGINT NOT NULL DEFAULT 0,
  duration_ms       BIGINT  NOT NULL DEFAULT 0,
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  upserted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, phase, attempt)
);

CREATE INDEX IF NOT EXISTS idx_phase_events_change
  ON phase_events(repo_root, change_id);

CREATE TABLE IF NOT EXISTS driver_sessions (
  repo_root         VARCHAR NOT NULL,
  change_id         VARCHAR NOT NULL,
  session_id        VARCHAR NOT NULL,
  model             VARCHAR,
  total_tokens      BIGINT  NOT NULL DEFAULT 0,
  input_tokens      BIGINT  NOT NULL DEFAULT 0,
  output_tokens     BIGINT  NOT NULL DEFAULT 0,
  cost_usd          DOUBLE  NOT NULL DEFAULT 0.0,
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  upserted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (repo_root, change_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_driver_sessions_change
  ON driver_sessions(repo_root, change_id);
