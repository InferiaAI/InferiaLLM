-- =============================================================================
-- GLOBAL DATABASE SCHEMA (InferiaLLM)
-- Combined Core (Filtration) and Orchestration Tables
-- =============================================================================

-- =============================================================================
-- PART 1: CORE AUTHENTICATION & USERS (Filtration)
-- =============================================================================

CREATE TABLE organizations (
    id VARCHAR NOT NULL, 
    name VARCHAR NOT NULL, 
    api_key VARCHAR, 
    log_payloads BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_organizations_name ON organizations (name);
CREATE UNIQUE INDEX ix_organizations_api_key ON organizations (api_key);
CREATE INDEX ix_organizations_id ON organizations (id);

CREATE TABLE users (
    id VARCHAR NOT NULL, 
    email VARCHAR NOT NULL, 
    password_hash VARCHAR NOT NULL, 
    totp_secret VARCHAR,
    totp_enabled BOOLEAN DEFAULT FALSE,
    default_org_id VARCHAR, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id)
);
CREATE INDEX ix_users_id ON users (id);
CREATE UNIQUE INDEX ix_users_email ON users (email);

-- =============================================================================
-- PART 2: ORCHESTRATION ENGINE (Pools, Inventory, Deployments)
-- =============================================================================

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
);
CREATE INDEX IF NOT EXISTS idx_provider_resources_provider_region
    ON public.provider_resources USING btree
    (provider ASC NULLS LAST, region COLLATE pg_catalog."default" ASC NULLS LAST);

-- ------------------------------------------------
-- PROVIDER CREDENTIALS TABLE (Universal for all providers)
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.provider_credentials
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    provider text COLLATE pg_catalog."default" NOT NULL,  -- 'nosana', 'akash', 'aws', etc.
    name text COLLATE pg_catalog."default" NOT NULL,
    credential_type text COLLATE pg_catalog."default" NOT NULL,  -- 'api_key', 'wallet', 'mnemonic', 'access_key'
    credential_value_encrypted text COLLATE pg_catalog."default" NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT provider_credentials_pkey PRIMARY KEY (id),
    CONSTRAINT provider_credentials_provider_name_key UNIQUE (provider, name)
);
CREATE INDEX IF NOT EXISTS idx_provider_credentials_provider
    ON public.provider_credentials USING btree
    (provider COLLATE pg_catalog."default" ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_provider_credentials_name
    ON public.provider_credentials USING btree
    (name COLLATE pg_catalog."default" ASC NULLS LAST);

-- Migrate old nosana_api_keys table if exists (for backward compatibility)
-- DROP TABLE IF EXISTS public.nosana_api_keys;

-- ------------------------------------------------
-- COMPUTE POOLS TABLE
-- -----------------------------------------------

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
    provider_credential_name text COLLATE pg_catalog."default",  -- References provider_credentials.name for this pool
    CONSTRAINT compute_pools_pkey PRIMARY KEY (id),
    CONSTRAINT compute_pools_pool_name_owner_type_owner_id_key UNIQUE (pool_name, owner_type, owner_id)
);
-- Note: provider_credential_name validation is enforced at application level
-- in ComputePoolManagerService to allow flexible credential lifecycle management
CREATE INDEX IF NOT EXISTS idx_compute_pools_provider
    ON public.compute_pools USING btree
    (provider ASC NULLS LAST);

-- ------------------------------------------------
-- COMPUTE INVENTORY (NODES)
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.compute_inventory
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    pool_id uuid NOT NULL,
    provider provider_type NOT NULL,
    provider_instance_id text COLLATE pg_catalog."default" NOT NULL,
    provider_resource_id uuid,
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
);
CREATE INDEX IF NOT EXISTS idx_inventory_heartbeat
    ON public.compute_inventory USING btree
    (last_heartbeat ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_inventory_pool_state
    ON public.compute_inventory USING btree
    (pool_id ASC NULLS LAST, state ASC NULLS LAST);

-- ------------------------------------------------
-- WORKLOAD ASSIGNMENTS
-- ------------------------------------------------

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
);
CREATE INDEX IF NOT EXISTS idx_workload_node
    ON public.workload_assignments USING btree
    (node_id ASC NULLS LAST);

-- ------------------------------------------------
-- ALLOCATIONS
-- ------------------------------------------------

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
);
CREATE INDEX IF NOT EXISTS idx_allocations_node
    ON public.allocations USING btree
    (node_id ASC NULLS LAST)
    WHERE released_at IS NULL;

-- ------------------------------------------------
-- AUTOSCALER STATE
-- ------------------------------------------------

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
);

-- ------------------------------------------------
-- BILLING EVENTS
-- ------------------------------------------------

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
);

-- ------------------------------------------------
-- GANG JOBS
-- ------------------------------------------------

CREATE TABLE IF NOT EXISTS public.gang_jobs
(
    job_id uuid NOT NULL,
    owner_type text COLLATE pg_catalog."default" NOT NULL,
    owner_id text COLLATE pg_catalog."default" NOT NULL,
    gang_size integer NOT NULL,
    state text COLLATE pg_catalog."default" NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT gang_jobs_pkey PRIMARY KEY (job_id)
);

-- ------------------------------------------------
-- MODEL REGISTRY
-- ------------------------------------------------

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
);
CREATE INDEX IF NOT EXISTS idx_model_registry_name
    ON public.model_registry USING btree
    (name COLLATE pg_catalog."default" ASC NULLS LAST);

-- ------------------------------------------------
-- MODEL DEPLOYMENTS (Unified Table)
-- ------------------------------------------------

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
    inference_model text, -- Backend model slug (e.g. 'meta-llama/...')
    model_type text DEFAULT 'inference', -- Model type: inference, embedding, image_generation, etc.

    pool_id uuid NOT NULL,
    replicas integer NOT NULL,
    gpu_per_replica integer NOT NULL,
    state text COLLATE pg_catalog."default" NOT NULL DEFAULT 'PENDING',
    llmd_resource_name text COLLATE pg_catalog."default",
    allocation_ids uuid[],
    node_ids uuid[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    error_message text,
    CONSTRAINT model_deployments_pkey PRIMARY KEY (deployment_id),
    CONSTRAINT model_deployments_model_id_fkey FOREIGN KEY (model_id)
        REFERENCES public.model_registry (model_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT model_deployments_pool_id_fkey FOREIGN KEY (pool_id)
        REFERENCES public.compute_pools (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_model_deployments_state
    ON public.model_deployments USING btree
    (state COLLATE pg_catalog."default" ASC NULLS LAST);

-- ------------------------------------------------
-- QUOTAS & SNAPSHOTS
-- ------------------------------------------------

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
);

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
);

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


-- =============================================================================
-- PART 3: ADDITIONAL CORE LOGIC (Filtration)
-- (Dependent on Core Orgs/Users and Orchestration Model Deployments)
-- =============================================================================

CREATE TABLE usage_stats (
    id VARCHAR NOT NULL, 
    user_id VARCHAR NOT NULL, 
    date DATE NOT NULL, 
    model VARCHAR NOT NULL, 
    prompt_tokens INTEGER, 
    completion_tokens INTEGER, 
    total_tokens INTEGER, 
    request_count INTEGER, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT _user_daily_model_usage_uc UNIQUE (user_id, date, model)
);
CREATE INDEX ix_usage_stats_user_id ON usage_stats (user_id);
CREATE INDEX ix_usage_stats_id ON usage_stats (id);
CREATE INDEX ix_usage_stats_model ON usage_stats (model);

CREATE TABLE roles (
    name VARCHAR NOT NULL, 
    description VARCHAR, 
    permissions JSON NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (name)
);

CREATE TABLE policies (
    id VARCHAR NOT NULL, 
    policy_type VARCHAR NOT NULL, 
    config_json JSON NOT NULL, 
    org_id VARCHAR NOT NULL, 
    deployment_id UUID, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(org_id) REFERENCES organizations (id), 
    FOREIGN KEY(deployment_id) REFERENCES model_deployments (deployment_id) ON DELETE SET NULL
);
CREATE INDEX ix_policies_id ON policies (id);

CREATE TABLE api_keys (
    id VARCHAR NOT NULL, 
    name VARCHAR NOT NULL, 
    key_hash VARCHAR NOT NULL, 
    prefix VARCHAR NOT NULL, 
    org_id VARCHAR NOT NULL, 
    deployment_id UUID, 
    is_active BOOLEAN, 
    last_used_at TIMESTAMP WITHOUT TIME ZONE, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    UNIQUE (key_hash), 
    FOREIGN KEY(deployment_id) REFERENCES model_deployments (deployment_id) ON DELETE SET NULL
);

CREATE TABLE inference_logs (
    id VARCHAR NOT NULL, 
    deployment_id UUID NOT NULL, 
    user_id VARCHAR NOT NULL, 
    ip_address VARCHAR,
    request_payload JSON, 
    model VARCHAR NOT NULL, 
    latency_ms INTEGER, 
    ttft_ms INTEGER, 
    tokens_per_second FLOAT, 
    prompt_tokens INTEGER, 
    completion_tokens INTEGER, 
    total_tokens INTEGER, 
    status_code INTEGER, 
    error_message VARCHAR, 
    is_streaming BOOLEAN, 
    applied_policies JSON,
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(deployment_id) REFERENCES model_deployments (deployment_id) ON DELETE CASCADE
);
CREATE INDEX ix_inference_logs_user_id ON inference_logs (user_id);
CREATE INDEX ix_inference_logs_ip_address ON inference_logs (ip_address);
CREATE INDEX ix_inference_logs_id ON inference_logs (id);
CREATE INDEX ix_inference_logs_model ON inference_logs (model);
CREATE INDEX ix_inference_logs_created_at ON inference_logs (created_at);
CREATE INDEX ix_inference_logs_deployment_id ON inference_logs (deployment_id);

CREATE TABLE invitations (
    id VARCHAR NOT NULL, 
    email VARCHAR NOT NULL, 
    role VARCHAR, 
    token VARCHAR NOT NULL, 
    org_id VARCHAR NOT NULL, 
    created_by VARCHAR NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    accepted_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(org_id) REFERENCES organizations (id), 
    FOREIGN KEY(created_by) REFERENCES users (id)
);
CREATE INDEX ix_invitations_email ON invitations (email);
CREATE INDEX ix_invitations_id ON invitations (id);
CREATE UNIQUE INDEX ix_invitations_token ON invitations (token);

CREATE TABLE user_organizations (
    id VARCHAR NOT NULL, 
    user_id VARCHAR NOT NULL, 
    org_id VARCHAR NOT NULL, 
    role VARCHAR, 
    created_at TIMESTAMP WITHOUT TIME ZONE, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_user_org UNIQUE (user_id, org_id), 
    FOREIGN KEY(user_id) REFERENCES users (id), 
    FOREIGN KEY(org_id) REFERENCES organizations (id)
);
CREATE INDEX ix_user_organizations_id ON user_organizations (id);

CREATE TABLE audit_logs (
    id VARCHAR NOT NULL, 
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    user_id VARCHAR, 
    action VARCHAR NOT NULL, 
    resource_type VARCHAR, 
    resource_id VARCHAR, 
    details JSON, 
    ip_address VARCHAR, 
    status VARCHAR NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id);
CREATE INDEX ix_audit_logs_action ON audit_logs (action);

CREATE TABLE system_settings (
    key VARCHAR NOT NULL, 
    value JSON NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (key)
);
CREATE INDEX ix_system_settings_key ON system_settings (key);
