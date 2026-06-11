-- Migration: Add deployment_terminal_logs table for persisting terminal output
-- on deployment failures, stops, and terminations.

CREATE TABLE IF NOT EXISTS public.deployment_terminal_logs
(
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    deployment_id uuid NOT NULL,
    log_lines text[] NOT NULL DEFAULT '{}',
    captured_at timestamp with time zone NOT NULL DEFAULT now(),
    trigger_event text NOT NULL,  -- e.g. 'FAILED', 'STOPPED', 'TERMINATED'
    CONSTRAINT deployment_terminal_logs_pkey PRIMARY KEY (id),
    CONSTRAINT deployment_terminal_logs_deployment_fkey FOREIGN KEY (deployment_id)
        REFERENCES public.model_deployments (deployment_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_deployment_terminal_logs_deployment_id
    ON public.deployment_terminal_logs USING btree (deployment_id);
