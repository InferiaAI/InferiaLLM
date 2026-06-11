import logging
from uuid import UUID

from orchestration.placement_engine.scoring import score_node
from orchestration.adapter_engine.registry import get_adapter

log = logging.getLogger(__name__)

MAX_PROVISION_RETRIES = 4
PROVISION_WAIT_SECONDS = 40


class ModelDeploymentWorker:
    def __init__(
        self,
        *,
        deployment_repo,
        model_registry_repo,
        pool_repo,
        placement_repo,
        scheduler,
        inventory_repo,
        runtime_resolver,
        runtime_strategies,  # dict: {"vllm": ..., "llmd": ...}
        worker_controller=None,
    ):
        self.deployments = deployment_repo
        self.models = model_registry_repo
        self.pools = pool_repo
        self.placement = placement_repo
        self.scheduler = scheduler
        self.inventory = inventory_repo
        self.runtime_resolver = runtime_resolver
        self.strategies = runtime_strategies
        # Optional: the worker-side controller used to unload models over the
        # WS channel during a reconciler-managed teardown (gRPC delete path).
        # When absent the shared terminate core simply skips the best-effort
        # unload_model step.
        self.worker_controller = worker_controller

    # -------------------------------------------------
    # EVENT HANDLER
    # -------------------------------------------------
    async def handle_deploy_requested(self, deployment_id: UUID):
        d = await self.deployments.get(deployment_id)
        if not d:
            log.warning(
                f"Skipping deploy for {deployment_id} because deployment not found in database"
            )
            return

        current_state = d.get("state")
        log.info(f"Handling deploy request for {deployment_id}. State: {current_state}")

        if current_state not in ("PENDING", None):
            log.warning(
                f"Skipping deploy for {deployment_id} because state is not PENDING (current: {current_state})"
            )
            return

        # Treat NULL state as PENDING - fix for deployments with NULL state
        if current_state is None:
            log.info(f"Deployment {deployment_id} has NULL state, treating as PENDING")
            # First set the NULL state to PENDING explicitly so the CAS can match it
            await self.deployments.update_state(deployment_id, "PENDING")
            d = dict(d)
            d["state"] = "PENDING"

        updated = await self.deployments.update_state_if(
            deployment_id, expected_state="PENDING", new_state="PROVISIONING"
        )
        if not updated:
            log.warning(
                f"Could not acquire deployment {deployment_id} - state may have changed"
            )
            return

        model = None
        if d.get("model_id"):
            try:
                model = await self.models.get_model_by_id(d["model_id"])
            except Exception:
                log.warning(
                    f"Failed to fetch model {d['model_id']} from registry, proceeding with config if available."
                )
        pool = await self.pools.get(d["pool_id"])
        resources_required = await self.inventory.get_resource_requirement(d["pool_id"])

        # Get adapter and capabilities early
        adapter = None
        capabilities = None
        if pool:
            try:
                adapter = get_adapter(pool["provider"])
                capabilities = adapter.get_capabilities()
            except Exception as e:
                log.warning(f"Could not get adapter for {pool['provider']}: {e}")

        # Check if this is a cluster-based pool (e.g., SkyPilot)
        is_cluster_pool = pool and pool.get("pool_type") == "cluster"
        cluster_id = pool.get("cluster_id") if pool else None

        node_spec = None

        # ------------------------------------
        # CLUSTER-BASED DEPLOYMENT (SkyPilot)
        # ------------------------------------
        if (
            is_cluster_pool
            and cluster_id
            and adapter
            and capabilities
            and capabilities.supports_cluster_mode
        ):
            try:
                log.info(f"Deploying to cluster-based pool: cluster_id={cluster_id}")

                await self.deployments.update_state(deployment_id, "PROVISIONING")

                # Build service configuration from deployment config
                metadata = {}
                if d.get("configuration"):
                    import json

                    config = d["configuration"]
                    if isinstance(config, str):
                        try:
                            config = json.loads(config)
                        except json.JSONDecodeError:
                            config = {}
                    metadata = config

                if d.get("inference_model"):
                    metadata["model_id"] = d["inference_model"]
                if d.get("model_name"):
                    metadata["model_name"] = d["model_name"]
                if d.get("engine"):
                    metadata["engine"] = d["engine"]

                # Get image from metadata or use default
                image = metadata.get("image", "vllm/vllm:latest")

                # Prepare ports (handle both 'ports' and 'expose' keys)
                ports = metadata.get("ports") or metadata.get("expose") or [{"port": 8000, "type": "http"}]

                # Prepare environment variables
                env = metadata.get("env", {})
                if d.get("model_name"):
                    env["MODEL_NAME"] = d["model_name"]

                # Deploy service on cluster
                service_name = f"deploy-{deployment_id.hex[:8]}"
                expose_url = await adapter.deploy_service(
                    cluster_id=cluster_id,
                    service_name=service_name,
                    image=image,
                    ports=ports,
                    env=env,
                    cmd=metadata.get("cmd"),
                    provider_credential_name=pool.get("provider_credential_name"),
                )

                log.info(
                    f"Deployed service {service_name} on cluster {cluster_id}, URL: {expose_url}"
                )

                if expose_url:
                    await self.deployments.update_endpoint(
                        deployment_id=deployment_id,
                        endpoint=expose_url,
                        model_name=d.get("model_name"),
                    )

                # Resolve real cluster specs from adapter
                cluster_status = await adapter.get_cluster_status(
                    cluster_id=cluster_id,
                    provider_credential_name=pool.get("provider_credential_name"),
                )

                # GPU type specs for fallback (per GPU)
                GPU_TYPE_SPECS = {
                    "A100": {"vcpu": 12, "ram_gb": 85},
                    "A100-80GB": {"vcpu": 12, "ram_gb": 85},
                    "A10G": {"vcpu": 4, "ram_gb": 16},
                    "T4": {"vcpu": 4, "ram_gb": 16},
                    "L4": {"vcpu": 8, "ram_gb": 32},
                    "V100": {"vcpu": 8, "ram_gb": 61},
                    "H100": {"vcpu": 26, "ram_gb": 200},
                }
                gpu_type = (pool.get("allowed_gpu_types") or [""])[0]
                gpu_count = pool.get("gpu_count") or 1
                type_specs = GPU_TYPE_SPECS.get(gpu_type, {"vcpu": 8, "ram_gb": 32})
                cluster_vcpu = type_specs["vcpu"] * gpu_count
                cluster_ram = type_specs["ram_gb"] * gpu_count

                # Register service in inventory (not a real node, but tracks the deployment)
                node_id = await self.inventory.register_node(
                    pool_id=d["pool_id"],
                    provider=pool["provider"],
                    provider_instance_id=f"{cluster_id}/{service_name}",  # Composite ID
                    provider_resource_id=None,
                    hostname=cluster_status.get("head_ip") or cluster_id,
                    gpu_total=gpu_count,
                    vcpu_total=cluster_vcpu,
                    ram_gb_total=cluster_ram,
                    state="ready",
                    node_class="cluster",
                    metadata={
                        "service_name": service_name,
                        "cluster_id": cluster_id,
                        "image": image,
                    },
                    expose_url=expose_url,
                )

                await self.deployments.attach_runtime(
                    deployment_id=deployment_id,
                    allocation_ids=[],
                    node_ids=[node_id] if node_id else [],
                    runtime=pool["provider"],
                )

                await self.deployments.update_state(deployment_id, "RUNNING")
                log.info(
                    f"Cluster-based deployment {deployment_id} is RUNNING on cluster {cluster_id}"
                )
                return

            except Exception as e:
                log.error(f"Cluster-based deployment failed for {deployment_id}: {e}")
                await self.deployments.update_state(
                    deployment_id,
                    "FAILED",
                    error_message=f"Cluster deployment failed: {str(e)}",
                )
                return

        # ------------------------------------
        # JOB-BASED DEPLOYMENT (Nosana, Akash) or PLACEMENT
        # ------------------------------------

        node_spec = None
        try:
            # Determine resource needs (default to full node if not specified, or hardcoded fallback)
            vcpu_req = resources_required["vcpu_total"] if resources_required else 8
            ram_gb_req = (
                resources_required["ram_gb_total"] if resources_required else 32
            )

            # -------- CAPACITY LOOP --------
            candidates = []
            for attempt in range(MAX_PROVISION_RETRIES + 1):
                candidates = await self.placement.fetch_candidate_nodes(
                    pool_id=d["pool_id"],
                    gpu_req=d["gpu_per_replica"],
                    vcpu_req=vcpu_req,
                    ram_req=ram_gb_req,
                )
                log.info(
                    f"placement attempt={attempt} pool={d['pool_id']} "
                    f"gpu_req={d['gpu_per_replica']} vcpu_req={vcpu_req} ram_req={ram_gb_req} "
                    f"candidates={len(candidates)}"
                )

                if candidates:
                    break

                if attempt == MAX_PROVISION_RETRIES:
                    await self.deployments.update_state(
                        deployment_id,
                        "FAILED",
                        error_message=f"No available nodes after {MAX_PROVISION_RETRIES} provisioning attempts",
                    )
                    return

                adapter = get_adapter(pool["provider"])
                capabilities = adapter.get_capabilities()

                # Determine Metadata / Job Spec
                metadata = {}

                # 1. Use Configuration directly if available (Unified Schema)
                if d.get("configuration"):
                    import json

                    config = d["configuration"]
                    if isinstance(config, str):
                        try:
                            config = json.loads(config)
                        except json.JSONDecodeError:
                            config = {}
                    metadata = config

                # Inject model identifiers for job_builder (API key security)
                if d.get("inference_model"):
                    metadata["model_id"] = d["inference_model"]
                if d.get("model_name"):
                    metadata["model_name"] = d["model_name"]
                if d.get("engine"):
                    metadata["engine"] = d["engine"]

                # 2. Legacy / Registry Fallback (only if no configuration was set)
                if not metadata and model:
                    metadata = {
                        "image": model["artifact_uri"],
                        "cmd": [
                            "meta-llama/Llama-2-7b-chat-hf",
                            "--port",
                            "9000",
                        ],
                        "gpu": True,
                        "expose": [{"port": 9000, "type": "http"}],
                    }

                # 3. Last Resort / Error Check.
                # The provider adapter (e.g. Nosana) resolves the docker
                # image and cmd from the engine + model_id via job_builder
                # when those fields are present, so don't gate on the raw
                # legacy "image" / "cmd" keys when we already have enough
                # to build a spec.
                has_engine_or_model = bool(
                    metadata.get("engine")
                    or metadata.get("model_id")
                    or metadata.get("model_name")
                    or d.get("engine")
                    or d.get("inference_model")
                )
                if (
                    not metadata.get("image")
                    and not metadata.get("cmd")
                    and metadata.get("workload_type") != "training"
                    and not has_engine_or_model
                ):
                    log.error(f"Missing job definition for deployment {deployment_id}")
                    await self.deployments.update_state(
                        deployment_id,
                        "FAILED",
                        error_message="Missing job definition or image for deployment",
                    )
                    return

                # ----------------------------------------------------------
                # FENCE: reconciler-managed providers must NOT be provisioned
                # through this legacy path.  PulumiAWSAdapter.provision_node
                # (and WorkerAdapter.provision_node) raise NotImplementedError
                # because they were intentionally removed in T10/T23.  Short-
                # circuit here so the failure is LOUD and actionable rather
                # than an opaque NotImplementedError that leaks into the outer
                # except and produces a cryptic error_message.
                # ----------------------------------------------------------
                _RECONCILER_MANAGED_PROVIDERS = frozenset(
                    {"aws", "gcp", "azure", "on_prem", "worker"}
                )
                _provider = pool.get("provider", "")
                if _provider in _RECONCILER_MANAGED_PROVIDERS:
                    _msg = (
                        f"resume/auto-provision for provider '{_provider}' must go "
                        "through the pool-first reconciler (POST /deployment/start or "
                        "/deploy), not the legacy deploy-requested worker"
                    )
                    log.error(
                        f"Fencing legacy provision_node call for deployment "
                        f"{deployment_id}: provider={_provider!r} is reconciler-managed"
                    )
                    await self.deployments.update_state(
                        deployment_id,
                        "FAILED",
                        error_message=_msg,
                    )
                    return

                node_spec = await adapter.provision_node(
                    provider_resource_id=pool["allowed_gpu_types"][0],
                    pool_id=pool["provider_pool_id"],
                    metadata=metadata,
                    provider_credential_name=pool.get("provider_credential_name"),
                )

                # Handle simulation mode (provider-agnostic)
                if node_spec.get("metadata", {}).get("mode") == "simulation":
                    await self.deployments.attach_runtime(
                        deployment_id=deployment_id,
                        allocation_ids=[],
                        node_ids=[],
                        runtime=f"{pool['provider']}-sim",
                    )
                    await self.deployments.update_state(deployment_id, "RUNNING")
                    return

                # ---- Universal Readiness Poll ----
                timeout = capabilities.readiness_timeout_seconds
                expose_url = await adapter.wait_for_ready(
                    provider_instance_id=node_spec["provider_instance_id"],
                    timeout=timeout,
                    provider_credential_name=pool.get("provider_credential_name"),
                )

                # SAFETY CHECK — deployment may have been cancelled during provisioning
                d_latest = await self.deployments.get(deployment_id)
                if not d_latest or d_latest["state"] != "PROVISIONING":
                    log.warning(
                        f"Deployment {deployment_id} state changed to "
                        f"{d_latest.get('state') if d_latest else 'None'} during provisioning. Aborting."
                    )
                    if node_spec and node_spec.get("provider_instance_id"):
                        try:
                            cleanup_adapter = get_adapter(pool["provider"])
                            await cleanup_adapter.deprovision_node(
                                provider_instance_id=node_spec["provider_instance_id"],
                                provider_credential_name=pool.get("provider_credential_name"),
                            )
                        except Exception as cleanup_err:
                            log.warning(f"Failed to cleanup orphaned node on abort: {cleanup_err}")
                    return

                if not expose_url or expose_url.endswith("-ready"):
                    expose_url = expose_url or node_spec.get("expose_url")

                if not expose_url and node_spec.get("expose_url"):
                    expose_url = node_spec.get("expose_url")

                if expose_url:
                    await self.deployments.update_endpoint(
                        deployment_id=deployment_id,
                        endpoint=expose_url,
                        model_name=d.get("model_name"),
                    )

                node_id = await self.inventory.register_node(
                    pool_id=d["pool_id"],
                    provider=node_spec["provider"],
                    provider_instance_id=node_spec["provider_instance_id"],
                    provider_resource_id=None,
                    hostname=node_spec["hostname"],
                    gpu_total=node_spec["gpu_total"],
                    vcpu_total=node_spec["vcpu_total"],
                    ram_gb_total=node_spec["ram_gb_total"],
                    state="ready",
                    node_class=node_spec["node_class"],
                    metadata=node_spec["metadata"],
                    expose_url=expose_url,
                )

                if node_id:
                    await self.deployments.attach_runtime(
                        deployment_id=deployment_id,
                        allocation_ids=[],
                        node_ids=[node_id],
                        runtime=pool["provider"],
                    )
                    log.info(
                        f"Deployment {deployment_id} on {pool['provider']} attached node_id {node_id}."
                    )
                else:
                    log.warning(
                        f"Deployment {deployment_id} on {pool['provider']} "
                        f"has no node_id returned from register_node."
                    )

                await self.deployments.update_state(deployment_id, "RUNNING")

                # For ephemeral providers, deployment is complete once provisioned
                if capabilities.is_ephemeral:
                    return

            # -------- PLACEMENT --------
            if not candidates:
                log.error(
                    f"Insufficient capacity for deployment {deployment_id}"
                )
                await self.deployments.update_state(
                    deployment_id,
                    "FAILED",
                    error_message=f"Insufficient capacity: GPU={d['gpu_per_replica']}, vCPU={vcpu_req}, RAM={ram_gb_req}",
                )
                return

            best_node = min(candidates, key=score_node)
            node_id = UUID(str(best_node["node_id"]))
            best_agent_kind = best_node.get("agent_kind") if isinstance(best_node, dict) else (
                best_node["agent_kind"] if "agent_kind" in best_node.keys() else None
            )

            await self.deployments.update_state(deployment_id, "SCHEDULING")
            await self.deployments.update_state(deployment_id, "DEPLOYING")

            runtime = self.runtime_resolver.resolve(
                replicas=d["replicas"],
                gpu_per_replica=d["gpu_per_replica"],
                engine=d.get("engine"),
                model_type=d.get("model_type"),
                agent_kind=best_agent_kind,
            )

            # Synthesize a model dict for engine-only deployments (no model registry row).
            if model is None:
                import json as _json
                cfg = d.get("configuration") or {}
                if isinstance(cfg, str):
                    try:
                        cfg = _json.loads(cfg)
                    except Exception:
                        cfg = {}
                raw_uri = (
                    d.get("inference_model")
                    or (cfg.get("model_id") if isinstance(cfg, dict) else None)
                    or d.get("model_name")
                    or ""
                )
                # Worker controller validates artifact_uri as scheme://path.
                # Bare HF IDs ("org/model") need the hf:// scheme.
                if raw_uri and "://" not in raw_uri:
                    artifact_uri = f"hf://{raw_uri}"
                else:
                    artifact_uri = raw_uri
                model = {
                    "artifact_uri": artifact_uri,
                    "backend": d.get("engine") or "vllm",
                    "format": "hf",
                    "config": cfg if isinstance(cfg, dict) else {},
                }

            strategy = self.strategies.get(runtime)
            if not strategy:
                raise RuntimeError(
                    f"No deployment strategy registered for runtime '{runtime}'"
                )

            result = await strategy.deploy(
                deployment_id=deployment_id,
                model=model,
                pool_id=d["pool_id"],
                node_id=node_id,
                replicas=d["replicas"],
                gpu_per_replica=d["gpu_per_replica"],
                vcpu_per_replica=vcpu_req,
                ram_gb_per_replica=ram_gb_req,
                workload_type=None,
            )

            allocation_ids = result.get("allocation_ids") or result.get("allocations")
            node_ids = result.get("node_ids")

            if allocation_ids and not isinstance(allocation_ids, list):
                allocation_ids = [allocation_ids]
            if node_ids and not isinstance(node_ids, list):
                node_ids = [node_ids]

            await self.deployments.attach_runtime(
                deployment_id=deployment_id,
                allocation_ids=allocation_ids,
                node_ids=node_ids,
                runtime=result["runtime"],
            )

            await self.deployments.update_state(deployment_id, "RUNNING")

        except Exception as e:
            log.exception(f"Deployment {deployment_id} failed: type={type(e).__name__} repr={e!r}")

            # Cleanup any orphaned node that was provisioned before the failure
            if node_spec and node_spec.get("provider_instance_id"):
                try:
                    cleanup_adapter = get_adapter(pool["provider"])
                    await cleanup_adapter.deprovision_node(
                        provider_instance_id=node_spec["provider_instance_id"],
                        provider_credential_name=pool.get("provider_credential_name"),
                    )
                except Exception as cleanup_err:
                    log.warning(
                        f"Failed to cleanup orphaned node for {deployment_id}: {cleanup_err}"
                    )

            d_current = await self.deployments.get(deployment_id)
            if d_current and d_current["state"] not in (
                "STOPPED",
                "TERMINATED",
                "TERMINATING",
            ):
                await self.deployments.update_state(
                    deployment_id,
                    "FAILED",
                    error_message=str(e),
                )
            else:
                log.info(
                    f"Skipping FAILED state update for {deployment_id} — "
                    f"already in terminal state: {d_current['state'] if d_current else 'deleted'}"
                )

    # Providers whose nodes are owned by the pool-first reconciler. For these,
    # teardown MUST go through the node-scoped refcount-aware path
    # (terminate_deployment_core -> _initiate_node_destroy -> force_cancel ->
    # CancelHandler runs `pulumi destroy inferia-<node_id>`), NOT the legacy
    # adapter.deprovision_node below (which destroys the POOL-scoped stack and
    # does no refcount release -> EC2 leak / wrong teardown). Matches the
    # _RECONCILER_MANAGED_PROVIDERS fence in handle_deploy_requested (Task 1.4).
    _RECONCILER_MANAGED_PROVIDERS = frozenset(
        {"aws", "gcp", "azure", "on_prem", "worker"}
    )

    async def handle_terminate_requested(self, deployment_id: UUID):
        d = await self.deployments.get(deployment_id)
        if not d:
            return

        if d["state"] != "TERMINATING":
            return

        # Get pool info to check if it's a cluster-based pool
        pool = None
        is_cluster_pool = False
        cluster_id = None
        try:
            pool = await self.pools.get(d["pool_id"])
            if pool:
                is_cluster_pool = pool.get("pool_type") == "cluster"
                cluster_id = pool.get("cluster_id")
        except Exception:
            pass

        # ------------------------------------------------------------------
        # RECONCILER-MANAGED PROVIDERS: reroute to the SHARED refcount-aware,
        # node-scoped teardown. This unifies the gRPC delete path
        # (DeleteDeployment -> controller.request_delete -> this handler) with
        # the REST POST /deployment/terminate path: both now release the GPU,
        # unbind, and (only when no other live deploy references the node)
        # force_cancel the node's reconciler job so the reconciler destroys the
        # correct node-scoped stack. The legacy deprovision_node loop below is
        # fenced off for these providers — it tore down the wrong (pool-scoped)
        # stack and leaked the EC2.
        # ------------------------------------------------------------------
        provider = (pool or {}).get("provider")
        if provider in self._RECONCILER_MANAGED_PROVIDERS:
            await self._terminate_via_reconciler(deployment_id, pool=pool)
            return

        cleanup_error = None
        try:
            # ------------------------------------
            # 1. STOP RUNTIME (External providers / vLLM / etc)
            # ------------------------------------
            # Use node_ids to find the exact running instances to stop
            if d.get("node_ids"):
                for node_id in d["node_ids"]:
                    node = await self.inventory.get_node_by_id(node_id)
                    if node:
                        adapter = get_adapter(node["provider"])
                        metadata = node.get("metadata", {})
                        if isinstance(metadata, str):
                            try:
                                import json

                                metadata = json.loads(metadata)
                            except Exception:
                                metadata = {}

                        # Check if this is a cluster-based deployment
                        service_name = metadata.get("service_name") if metadata else None

                        if is_cluster_pool and service_name and cluster_id:
                            # For cluster-based pools: stop the service but keep the cluster
                            log.info(
                                f"Stopping service {service_name} on cluster {cluster_id}"
                            )
                            try:
                                await adapter.stop_service(
                                    cluster_id=cluster_id,
                                    service_name=service_name,
                                    provider_credential_name=metadata.get(
                                        "provider_credential_name"
                                    ),
                                )
                                log.info(
                                    f"Stopped service {service_name} on cluster {cluster_id}"
                                )
                            except Exception as e:
                                log.warning(f"Failed to stop service {service_name}: {e}")
                        else:
                            # For job-based pools: deprovision the node
                            log.info(f"Deprovisioning {node['provider']} node {node_id}")
                            await adapter.deprovision_node(
                                provider_instance_id=node["provider_instance_id"],
                                provider_credential_name=metadata.get(
                                    "provider_credential_name"
                                ),
                            )

            log.info(f"Stopped runtime for deployment {deployment_id}")

            # ------------------------------------
            # 2. RELEASE SCHEDULER ALLOCATIONS
            # ------------------------------------
            if d.get("allocation_ids"):
                for alloc_id in d["allocation_ids"]:
                    await self.scheduler.release(allocation_id=alloc_id)

            log.info(f"Released scheduler allocations for deployment {deployment_id}")

            # ------------------------------------
            # 3. HANDLE INVENTORY
            # ------------------------------------
            node = None
            if d.get("node_ids"):
                for node_id in d["node_ids"]:
                    node = await self.inventory.get_node_by_id(node_id)

                    # Use adapter capabilities to determine if ephemeral
                    is_ephemeral = False
                    is_cluster = False
                    if node:
                        try:
                            adapter = get_adapter(node["provider"])
                            capabilities = adapter.get_capabilities()
                            is_ephemeral = capabilities.is_ephemeral
                            is_cluster = capabilities.supports_cluster_mode
                        except Exception as e:
                            log.warning(
                                f"Could not get capabilities for {node['provider']}: {e}"
                            )
                            # Fallback: check if provider is known to be ephemeral
                            is_ephemeral = node.get("provider") in ["nosana", "akash"]

                    # For cluster-based deployments: always recycle (cluster stays alive)
                    # For ephemeral job-based: mark as terminated
                    # For persistent: recycle
                    if is_cluster:
                        await self.inventory.recycle_node(node_id)
                        log.info(
                            f"Recycled cluster service node {node_id} (cluster remains alive)"
                        )
                    elif is_ephemeral:
                        await self.inventory.mark_terminated(node_id)
                        log.info(f"Terminated ephemeral node {node_id}")
                    else:
                        await self.inventory.recycle_node(node_id)
                        log.info(f"Recycled inventory node {node_id}")
        except Exception as e:
            cleanup_error = e
            log.error(f"Cleanup failed for deployment {deployment_id}: {e}")
        finally:
            # ------------------------------------
            # 4. FINAL STATE — always reached
            # ------------------------------------
            final_state = "STOPPED" if cleanup_error is None else "FAILED"
            await self.deployments.update_state(deployment_id, final_state)
            if cleanup_error:
                log.error(f"Deployment {deployment_id} marked FAILED due to cleanup error: {cleanup_error}")
            else:
                log.info(f"Deployment {deployment_id} stopped")

    async def _terminate_via_reconciler(self, deployment_id: UUID, *, pool=None):
        """Drive the SHARED refcount-aware, node-scoped teardown for a
        reconciler-managed deploy (gRPC delete path).

        Builds the ``deps`` namespace ``terminate_deployment_core`` expects from
        this worker's own repos (``self.deployments.db`` is the asyncpg pool;
        the deployment/inventory/pool repos are reused, the provisioning-job
        repo is constructed from the pool, and the optional
        ``self.worker_controller`` provides ``unload_model``). The core releases
        the GPU, unbinds, and force_cancels the node's reconciler job when the
        refcount hits zero — the EXACT behaviour of POST /deployment/terminate.

        This runs inside an event consumer (no HTTP context), so the core's
        HTTPException signals are caught and logged here rather than propagated.
        """
        from orchestration.model_deployment.deployment_server import (
            _build_terminate_deps,
            terminate_deployment_core,
        )

        db_pool = getattr(self.deployments, "db", None)
        if db_pool is None:
            log.error(
                "Cannot route deployment %s through the reconciler teardown: "
                "deployment repo has no db pool; falling back is unsafe, "
                "marking FAILED",
                deployment_id,
            )
            await self.deployments.update_state(
                deployment_id,
                "FAILED",
                error_message="reconciler teardown unavailable (no db pool)",
            )
            return

        deps = _build_terminate_deps(
            db_pool,
            controller=self.worker_controller,
            event_bus=getattr(self.deployments, "event_bus", None),
            inventory=self.inventory,
            deploys=self.deployments,
            pool_repo=self.pools,
        )
        try:
            result = await terminate_deployment_core(deployment_id, deps=deps)
            log.info(
                "Routed deployment %s through reconciler-managed teardown: %s",
                deployment_id, result,
            )
        except Exception:
            # HTTPException (404 unknown deploy / 502 destroy-enqueue failure) or
            # any unexpected error. The core already updated DB state + flagged
            # the node terminating where it could; surface loudly and leave the
            # row in whatever terminal state the core reached. We deliberately do
            # NOT force the row to STOPPED here — the core owns the deploy state.
            log.exception(
                "Reconciler-managed teardown failed for deployment %s",
                deployment_id,
            )
