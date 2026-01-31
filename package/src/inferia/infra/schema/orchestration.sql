-- ------------------------------------------------
-- ENUMS (Shared System Constraints)
-- ------------------------------------------------

CREATE TYPE provider_type AS ENUM (
    'aws',
    'gcp',
    'azure',
    'nosana',
    'on_prem',
    'other'
);

CREATE TYPE pool_owner_type AS ENUM (
    'system',
    'organization',
    'user'
);

CREATE TYPE node_state AS ENUM (
    'provisioning',
    'ready',
    'busy',
    'draining',
    'unhealthy',
    'terminated'
);

CREATE TYPE pricing_model AS ENUM (
    'on_demand',
    'spot',
    'reserved',
    'fixed'
);

-- ------------------------------------------------
-- PROVIDER CAPACITY TABLE
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.provider_resources
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    provider provider_type NOT NULL,
    provider_resource_id text NOT NULL,
    gpu_type text,
    gpu_count integer DEFAULT 0,
    gpu_memory_gb integer,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    region text NOT NULL,
    zone text,
    pricing_model pricing_model NOT NULL,
    price_per_hour numeric(10,4),
    is_available boolean DEFAULT true,
    metadata jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT provider_resources_pkey PRIMARY KEY (id),
    CONSTRAINT provider_resources_provider_provider_resource_id_region_key
        UNIQUE (provider, provider_resource_id, region)
);

CREATE INDEX IF NOT EXISTS idx_provider_resources_provider_region
    ON public.provider_resources (provider, region);

-- ------------------------------------------------
-- COMPUTE POOLS TABLE
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.compute_pools
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    pool_name text NOT NULL,
    description text,
    owner_type pool_owner_type NOT NULL,
    owner_id text,
    provider provider_type NOT NULL,
    allowed_gpu_types text[],
    min_gpu_count integer DEFAULT 0,
    max_gpu_count integer,
    max_cost_per_hour numeric(10,4),
    region_constraint text[],
    scheduling_policy jsonb NOT NULL,
    autoscaling_policy jsonb,
    security_policy jsonb,
    is_dedicated boolean DEFAULT false,
    is_active boolean DEFAULT true,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    provider_pool_id text,
    CONSTRAINT compute_pools_pkey PRIMARY KEY (id),
    CONSTRAINT compute_pools_pool_name_owner_type_owner_id_key
        UNIQUE (pool_name, owner_type, owner_id)
);

CREATE INDEX IF NOT EXISTS idx_compute_pools_provider
    ON public.compute_pools (provider);

-- ------------------------------------------------
-- COMPUTE INVENTORY (NODES)
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.compute_inventory
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    pool_id uuid NOT NULL,
    provider provider_type NOT NULL,
    provider_instance_id text NOT NULL,
    provider_resource_id uuid,
    hostname text,
    gpu_total integer,
    gpu_allocated integer DEFAULT 0,
    vcpu_total integer,
    vcpu_allocated integer DEFAULT 0,
    ram_gb_total integer,
    ram_gb_allocated integer DEFAULT 0,
    state node_state NOT NULL,
    health_score integer DEFAULT 100,
    last_heartbeat timestamptz,
    metadata jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    node_class text NOT NULL DEFAULT 'on_demand',
    price_multiplier numeric(4,2) NOT NULL DEFAULT 1.0,
    expose_url text,
    CONSTRAINT compute_inventory_pkey PRIMARY KEY (id),
    CONSTRAINT compute_inventory_provider_provider_instance_id_key
        UNIQUE (provider, provider_instance_id),
    CONSTRAINT compute_inventory_pool_id_fkey
        FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id)
        ON DELETE CASCADE,
    CONSTRAINT compute_inventory_provider_resource_id_fkey
        FOREIGN KEY (provider_resource_id)
        REFERENCES public.provider_resources (id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_heartbeat
    ON public.compute_inventory (last_heartbeat);

CREATE INDEX IF NOT EXISTS idx_inventory_pool_state
    ON public.compute_inventory (pool_id, state);

-- ------------------------------------------------
-- ALLOCATIONS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.allocations
(
    allocation_id uuid PRIMARY KEY,
    node_id uuid NOT NULL,
    gpu integer NOT NULL,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    created_at timestamptz DEFAULT now(),
    released_at timestamptz,
    priority integer DEFAULT 0,
    preemptible boolean DEFAULT true,
    owner_type text NOT NULL,
    owner_id text NOT NULL,
    node_class text DEFAULT 'on_demand',
    job_id uuid,
    gang_size integer,
    gang_index integer,
    CONSTRAINT allocations_node_id_fkey
        FOREIGN KEY (node_id)
        REFERENCES public.compute_inventory (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_allocations_node
    ON public.allocations (node_id)
    WHERE released_at IS NULL;

-- ------------------------------------------------
-- AUTOSCALER STATE
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.autoscaler_state
(
    pool_id uuid PRIMARY KEY,
    last_scale_at timestamptz,
    consecutive_failures integer DEFAULT 0,
    CONSTRAINT autoscaler_state_pool_id_fkey
        FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id)
        ON DELETE CASCADE
);

-- ------------------------------------------------
-- BILLING EVENTS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.billing_events
(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_type text NOT NULL,
    owner_id text NOT NULL,
    allocation_id uuid NOT NULL,
    node_id uuid NOT NULL,
    event_type text NOT NULL,
    gpu integer NOT NULL,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    cost numeric(12,4) NOT NULL,
    occurred_at timestamptz DEFAULT now()
);

-- ------------------------------------------------
-- GANG JOBS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.gang_jobs
(
    job_id uuid PRIMARY KEY,
    owner_type text NOT NULL,
    owner_id text NOT NULL,
    gang_size integer NOT NULL,
    state text NOT NULL,
    created_at timestamptz DEFAULT now()
);

-- ------------------------------------------------
-- MODEL REGISTRY
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.model_registry
(
    model_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL,
    backend text NOT NULL,
    artifact_uri text NOT NULL,
    config jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT model_registry_name_version_key UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_model_registry_name
    ON public.model_registry (name);

-- ------------------------------------------------
-- MODEL DEPLOYMENTS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.model_deployments
(
    deployment_id uuid PRIMARY KEY,
    model_id uuid,
    model_name text,
    engine text,
    configuration jsonb,
    endpoint text,
    owner_id text,
    org_id text,
    policies jsonb,
    pool_id uuid NOT NULL,
    replicas integer NOT NULL,
    gpu_per_replica integer NOT NULL,
    state text NOT NULL,
    llmd_resource_name text,
    allocation_ids uuid[],
    node_ids uuid[],
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT model_deployments_model_id_fkey
        FOREIGN KEY (model_id)
        REFERENCES public.model_registry (model_id)
        ON DELETE CASCADE,
    CONSTRAINT model_deployments_pool_id_fkey
        FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_model_deployments_state
    ON public.model_deployments (state);

-- ------------------------------------------------
-- QUOTAS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.quotas
(
    owner_type text NOT NULL,
    owner_id text NOT NULL,
    max_gpu integer,
    max_vcpu integer,
    max_ram_gb integer,
    max_allocations integer,
    monthly_spend_cap numeric(12,4),
    hourly_spend_cap numeric(12,4),
    CONSTRAINT quotas_pkey PRIMARY KEY (owner_type, owner_id)
);

-- ------------------------------------------------
-- USAGE SNAPSHOT
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.usage_snapshot
(
    owner_type text NOT NULL,
    owner_id text NOT NULL,
    gpu_in_use integer DEFAULT 0,
    vcpu_in_use integer DEFAULT 0,
    ram_gb_in_use integer DEFAULT 0,
    allocations integer DEFAULT 0,
    monthly_spend numeric(12,4) DEFAULT 0,
    hourly_spend numeric(12,4) DEFAULT 0,
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT usage_snapshot_pkey PRIMARY KEY (owner_type, owner_id)
);

-- ------------------------------------------------
-- WORKLOAD ASSIGNMENTS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.workload_assignments
(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workload_id text NOT NULL,
    pool_id uuid,
    node_id uuid,
    gpu_allocated integer,
    vcpu_allocated integer,
    ram_gb_allocated integer,
    started_at timestamptz DEFAULT now(),
    finished_at timestamptz,
    status text,
    metadata jsonb,
    CONSTRAINT workload_assignments_node_id_fkey
        FOREIGN KEY (node_id)
        REFERENCES public.compute_inventory (id),
    CONSTRAINT workload_assignments_pool_id_fkey
        FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id)
);

CREATE INDEX IF NOT EXISTS idx_workload_node
    ON public.workload_assignments (node_id);

-- ------------------------------------------------
-- OUTBOX
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS outbox_events
(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL,
    error text,
    created_at timestamptz DEFAULT now(),
    published_at timestamptz,
    updated_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox_events (status, created_at);
