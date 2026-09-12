-- src/infra/schema/migrations/20260912_add_k8s_provider_type.sql
--
-- The adapter registry maps "k8s" to KubernetesAdapter and the provider API
-- advertises it, but provider_type had no such value, so Kubernetes pools
-- could never be created. "on_prem" is not a substitute: it resolves to the
-- worker adapter.

ALTER TYPE provider_type ADD VALUE IF NOT EXISTS 'k8s';
