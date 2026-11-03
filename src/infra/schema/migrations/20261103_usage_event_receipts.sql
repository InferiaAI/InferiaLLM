-- A Redis Stream is at-least-once.  The receipt is inserted in the same
-- transaction as the daily usage upsert, so replay after a crash cannot add
-- the same event to durable usage twice.
CREATE TABLE IF NOT EXISTS usage_event_receipts (
    event_id UUID PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
