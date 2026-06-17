-- Add group_id column to compute_inventory for Envoy proxy grouping.
-- Nodes sharing a group_id are load-balanced together behind the front Envoy;
-- nodes without one are routed separately.
ALTER TABLE compute_inventory
  ADD COLUMN IF NOT EXISTS group_id TEXT;
