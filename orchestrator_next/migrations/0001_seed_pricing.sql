-- timestamps stored UTC by convention
-- Seed migration: create pricing table and insert all models from config/pricing.yaml.
-- Rates source: config/pricing.yaml (verified 2026-04-20).
-- effective_from = '2025-01-01T00:00:00' for all initial rows.

CREATE TABLE IF NOT EXISTS pricing (
  model_id            VARCHAR NOT NULL,
  input_usd           DOUBLE  NOT NULL,
  output_usd          DOUBLE  NOT NULL,
  cache_read_usd      DOUBLE  NOT NULL,
  cache_creation_usd  DOUBLE,
  is_local            BOOLEAN NOT NULL DEFAULT FALSE,
  effective_from      TIMESTAMP NOT NULL,
  PRIMARY KEY (model_id, effective_from)
);

INSERT OR REPLACE INTO pricing
  (model_id, input_usd, output_usd, cache_read_usd, cache_creation_usd,
   is_local, effective_from)
VALUES
  ('claude-opus-4-7',                        15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-opus-4-6',                        15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-opus-4-5',                        15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-sonnet-4-6',                       3.00, 15.00, 0.30,  3.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-sonnet-4-5',                       3.00, 15.00, 0.30,  3.75, FALSE, '2025-01-01T00:00:00'),
  ('claude-haiku-4-5',                        0.80,  4.00, 0.08,  1.00, FALSE, '2025-01-01T00:00:00'),
  ('claude-haiku-4-5-20251001',               0.80,  4.00, 0.08,  1.00, FALSE, '2025-01-01T00:00:00'),
  ('qwen/qwen3-coder-30b-a3b-instruct',       0.25,  1.00, 0.25,  NULL, FALSE, '2025-01-01T00:00:00'),
  ('coder',                                   0.00,  0.00, 0.00,  NULL, TRUE,  '2025-01-01T00:00:00'),
  ('__default__',                            15.00, 75.00, 1.50, 18.75, FALSE, '2025-01-01T00:00:00');
