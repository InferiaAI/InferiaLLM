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


-- Table: public.provider_resources

-- DROP TABLE IF EXISTS public.provider_resources;

CREATE TABLE IF NOT EXISTS public.provider_resources
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    provider provider_type NOT NULL,
    provider_resource_id text COLLATE pg_catalog."default" NOT NULL,
    gpu_type text COLLATE pg_catalog."default",
    gpu_count integer DEFAULT 0,
    gpu_memory_gb integer,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    region text COLLATE pg_catalog."default" NOT NULL,
    zone text COLLATE pg_catalog."default",
    pricing_model pricing_model NOT NULL,
    price_per_hour numeric(10,4),
    is_available boolean DEFAULT true,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT provider_resources_pkey PRIMARY KEY (id),
    CONSTRAINT provider_resources_provider_provider_resource_id_region_key UNIQUE (provider, provider_resource_id, region)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.provider_resources
    OWNER to postgres;
-- Index: idx_provider_resources_provider_region

-- DROP INDEX IF EXISTS public.idx_provider_resources_provider_region;

CREATE INDEX IF NOT EXISTS idx_provider_resources_provider_region
    ON public.provider_resources USING btree
    (provider ASC NULLS LAST, region COLLATE pg_catalog."default" ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;


-- ------------------------------------------------
-- COMPUTE POOLS TABLE
-- -----------------------------------------------

-- Table: public.compute_pools

-- DROP TABLE IF EXISTS public.compute_pools;

CREATE TABLE IF NOT EXISTS public.compute_pools
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    pool_name text COLLATE pg_catalog."default" NOT NULL,
    description text COLLATE pg_catalog."default",
    owner_type pool_owner_type NOT NULL,
    owner_id text COLLATE pg_catalog."default",
    provider provider_type NOT NULL,
    allowed_gpu_types text[] COLLATE pg_catalog."default",
    min_gpu_count integer DEFAULT 0,
    max_gpu_count integer,
    max_cost_per_hour numeric(10,4),
    region_constraint text[] COLLATE pg_catalog."default",
    scheduling_policy jsonb NOT NULL,
    autoscaling_policy jsonb,
    security_policy jsonb,
    is_dedicated boolean DEFAULT false,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    provider_pool_id text COLLATE pg_catalog."default",
    CONSTRAINT compute_pools_pkey PRIMARY KEY (id),
    CONSTRAINT compute_pools_pool_name_owner_type_owner_id_key UNIQUE (pool_name, owner_type, owner_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.compute_pools
    OWNER to postgres;
-- Index: idx_compute_pools_provider

-- DROP INDEX IF EXISTS public.idx_compute_pools_provider;

CREATE INDEX IF NOT EXISTS idx_compute_pools_provider
    ON public.compute_pools USING btree
    (provider ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;

-- ------------------------------------------------
-- COMPUTE INVENTORY (NODES)
-- ------------------------------------------------

-- Table: public.compute_inventory

-- DROP TABLE IF EXISTS public.compute_inventory;

CREATE TABLE IF NOT EXISTS public.compute_inventory
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    pool_id uuid NOT NULL,
    provider provider_type NOT NULL,
    provider_instance_id text COLLATE pg_catalog."default" NOT NULL,
    provider_resource_id text COLLATE pg_catalog."default",
    hostname text COLLATE pg_catalog."default",
    gpu_total integer,
    gpu_allocated integer DEFAULT 0,
    vcpu_total integer,
    vcpu_allocated integer DEFAULT 0,
    ram_gb_total integer,
    ram_gb_allocated integer DEFAULT 0,
    state node_state NOT NULL,
    health_score integer DEFAULT 100,
    last_heartbeat timestamp with time zone,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    node_class text COLLATE pg_catalog."default" NOT NULL DEFAULT 'on_demand'::text,
    price_multiplier numeric(4,2) NOT NULL DEFAULT 1.0,
    expose_url text,
    CONSTRAINT compute_inventory_pkey PRIMARY KEY (id),
    CONSTRAINT compute_inventory_provider_provider_instance_id_key UNIQUE (provider, provider_instance_id),
    CONSTRAINT compute_inventory_pool_id_fkey FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT compute_inventory_provider_resource_id_fkey FOREIGN KEY (provider_resource_id)
        REFERENCES public.provider_resources (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.compute_inventory
    OWNER to postgres;
-- Index: idx_inventory_heartbeat

-- DROP INDEX IF EXISTS public.idx_inventory_heartbeat;

CREATE INDEX IF NOT EXISTS idx_inventory_heartbeat
    ON public.compute_inventory USING btree
    (last_heartbeat ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;
-- Index: idx_inventory_pool_state

-- DROP INDEX IF EXISTS public.idx_inventory_pool_state;

CREATE INDEX IF NOT EXISTS idx_inventory_pool_state
    ON public.compute_inventory USING btree
    (pool_id ASC NULLS LAST, state ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;

-- ------------------------------------------------
-- WORKLOAD ASSIGNMENTS
-- ------------------------------------------------

-- ------------------------------------------------
-- INDEXES (Critical for Scale)
-- ------------------------------------------------

CREATE INDEX idx_provider_resources_provider_region
    ON provider_resources(provider, region);

CREATE INDEX idx_compute_pools_provider
    ON compute_pools(provider);

CREATE INDEX idx_inventory_pool_state
    ON compute_inventory(pool_id, state);

CREATE INDEX idx_inventory_heartbeat
    ON compute_inventory(last_heartbeat);

CREATE INDEX idx_workload_node
    ON workload_assignments(node_id);


-- Table: public.allocations

-- DROP TABLE IF EXISTS public.allocations;

CREATE TABLE IF NOT EXISTS public.allocations
(
    allocation_id uuid NOT NULL,
    node_id uuid NOT NULL,
    gpu integer NOT NULL,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    released_at timestamp with time zone,
    priority integer NOT NULL DEFAULT 0,
    preemptible boolean NOT NULL DEFAULT true,
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    node_class text COLLATE pg_catalog."default" NOT NULL DEFAULT 'on_demand'::text,
    job_id uuid,
    gang_size integer,
    gang_index integer,
    CONSTRAINT allocations_pkey PRIMARY KEY (allocation_id),
    CONSTRAINT allocations_node_id_fkey FOREIGN KEY (node_id)
        REFERENCES public.compute_inventory (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.allocations
    OWNER to postgres;
-- Index: idx_allocations_node

-- DROP INDEX IF EXISTS public.idx_allocations_node;

CREATE INDEX IF NOT EXISTS idx_allocations_node
    ON public.allocations USING btree
    (node_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default
    WHERE released_at IS NULL;


-- Table: public.autoscaler_state

-- DROP TABLE IF EXISTS public.autoscaler_state;

CREATE TABLE IF NOT EXISTS public.autoscaler_state
(
    pool_id uuid NOT NULL,
    last_scale_at timestamp with time zone,
    consecutive_failures integer DEFAULT 0,
    CONSTRAINT autoscaler_state_pkey PRIMARY KEY (pool_id),
    CONSTRAINT autoscaler_state_pool_id_fkey FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.autoscaler_state
    OWNER to postgres;


-- Table: public.billing_events

-- DROP TABLE IF EXISTS public.billing_events;

CREATE TABLE IF NOT EXISTS public.billing_events
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    allocation_id uuid NOT NULL,
    node_id uuid NOT NULL,
    event_type text COLLATE pg_catalog."default" NOT NULL,
    gpu integer NOT NULL,
    vcpu integer NOT NULL,
    ram_gb integer NOT NULL,
    cost numeric(12,4) NOT NULL,
    occurred_at timestamp with time zone DEFAULT now(),
    CONSTRAINT billing_events_pkey PRIMARY KEY (id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.billing_events
    OWNER to postgres;


-- Table: public.gang_jobs

-- DROP TABLE IF EXISTS public.gang_jobs;

CREATE TABLE IF NOT EXISTS public.gang_jobs
(
    job_id uuid NOT NULL,
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    gang_size integer NOT NULL,
    state text COLLATE pg_catalog."default" NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT gang_jobs_pkey PRIMARY KEY (job_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.gang_jobs
    OWNER to postgres;


-- Table: public.model_deployments

-- DROP TABLE IF EXISTS public.model_deployments;

CREATE TABLE IF NOT EXISTS public.model_deployments
(
    deployment_id uuid NOT NULL,
    model_id uuid, -- Made nullable
    model_name text, -- Direct model name support
    
    -- UNIFIED DEPLOYMENT FIELDS
    engine text, -- e.g. 'vllm', 'tgi', 'python'
    configuration jsonb, -- e.g. vllm args, env vars
    endpoint text, -- The exposed internal/external URL
    owner_id text, -- Organization/User ID owning this deployment
    org_id text, -- Organization ID
    policies jsonb, -- Filtration policies

    pool_id uuid NOT NULL,
    replicas integer NOT NULL,
    gpu_per_replica integer NOT NULL,
    state text COLLATE pg_catalog."default" NOT NULL,
    error_message text,
    llmd_resource_name text COLLATE pg_catalog."default",
    allocation_ids uuid[],
    node_ids uuid[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT model_deployments_pkey PRIMARY KEY (deployment_id),
    CONSTRAINT model_deployments_model_id_fkey FOREIGN KEY (model_id)
        REFERENCES public.model_registry (model_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT model_deployments_pool_id_fkey FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.model_deployments
    OWNER to postgres;
-- Index: idx_model_deployments_state

-- DROP INDEX IF EXISTS public.idx_model_deployments_state;

CREATE INDEX IF NOT EXISTS idx_model_deployments_state
    ON public.model_deployments USING btree
    (state COLLATE pg_catalog."default" ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;


-- Table: public.model_registry

-- DROP TABLE IF EXISTS public.model_registry;

CREATE TABLE IF NOT EXISTS public.model_registry
(
    model_id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text COLLATE pg_catalog."default" NOT NULL,
    version text COLLATE pg_catalog."default" NOT NULL,
    backend text COLLATE pg_catalog."default" NOT NULL,
    artifact_uri text COLLATE pg_catalog."default" NOT NULL,
    config jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT model_registry_pkey PRIMARY KEY (model_id),
    CONSTRAINT model_registry_name_version_key UNIQUE (name, version)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.model_registry
    OWNER to postgres;
-- Index: idx_model_registry_name

-- DROP INDEX IF EXISTS public.idx_model_registry_name;

CREATE INDEX IF NOT EXISTS idx_model_registry_name
    ON public.model_registry USING btree
    (name COLLATE pg_catalog."default" ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;



-- Table: public.quotas

-- DROP TABLE IF EXISTS public.quotas;

CREATE TABLE IF NOT EXISTS public.quotas
(
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    max_gpu integer,
    max_vcpu integer,
    max_ram_gb integer,
    max_allocations integer,
    monthly_spend_cap numeric(12,4),
    hourly_spend_cap numeric(12,4),
    CONSTRAINT quotas_pkey PRIMARY KEY (owner_type, owner_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.quotas
    OWNER to postgres;

-- Table: public.usage_snapshot

-- DROP TABLE IF EXISTS public.usage_snapshot;

CREATE TABLE IF NOT EXISTS public.usage_snapshot
(
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    gpu_in_use integer NOT NULL DEFAULT 0,
    vcpu_in_use integer NOT NULL DEFAULT 0,
    ram_gb_in_use integer NOT NULL DEFAULT 0,
    allocations integer NOT NULL DEFAULT 0,
    monthly_spend numeric(12,4) NOT NULL DEFAULT 0,
    hourly_spend numeric(12,4) NOT NULL DEFAULT 0,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT usage_snapshot_pkey PRIMARY KEY (owner_type, owner_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.usage_snapshot
    OWNER to postgres;


-- Table: public.workload_assignments

-- DROP TABLE IF EXISTS public.workload_assignments;

CREATE TABLE IF NOT EXISTS public.workload_assignments
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    workload_id text COLLATE pg_catalog."default" NOT NULL,
    pool_id uuid,
    node_id uuid,
    gpu_allocated integer,
    vcpu_allocated integer,
    ram_gb_allocated integer,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone,
    status text COLLATE pg_catalog."default",
    metadata jsonb,
    CONSTRAINT workload_assignments_pkey PRIMARY KEY (id),
    CONSTRAINT workload_assignments_node_id_fkey FOREIGN KEY (node_id)
        REFERENCES public.compute_inventory (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT workload_assignments_pool_id_fkey FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.workload_assignments
    OWNER to postgres;
-- Index: idx_workload_node

-- DROP INDEX IF EXISTS public.idx_workload_node;

CREATE INDEX IF NOT EXISTS idx_workload_node
    ON public.workload_assignments USING btree
    (node_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True)
    TABLESPACE pg_default;


CREATE TABLE IF NOT EXISTS outbox_events (
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
ON outbox_events(status, created_at);
