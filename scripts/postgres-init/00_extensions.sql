-- Enable TimescaleDB and other required extensions on first DB start.
-- Idempotent: only runs once when the data volume is empty.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
