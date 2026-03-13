import asyncio
import logging
from uuid import UUID

from inferia.services.orchestration.services.placement_engine.scoring import score_node
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter
from inferia.services.orchestration.services.adapter_engine.base import (
    ProviderCapabilities,
)

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
    ):
        self.deployments = deployment_repo
        self.models = model_registry_repo
        self.pools = pool_repo
        self.placement = placement_repo
        self.scheduler = scheduler
        self.inventory = inventory_repo
        self.runtime_resolver = runtime_resolver
        self.strategies = runtime_strategies

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
                    # Ensure it's a dict (it should be since it's JSONB/dict)
                    import json

                    config = d["configuration"]
                    if isinstance(config, str):
                        try:
                            config = json.loads(config)
                        except json.JSONDecodeError:
                            config = {}
                    # Update metadata with config, which now includes workload_type
                    metadata = config

                # Inject model identifiers for job_builder (API key security)
                if d.get("inference_model"):
                    metadata["model_id"] = d["inference_model"]
                if d.get("model_name"):
                    metadata["model_name"] = d["model_name"]
                if d.get("engine"):
                    metadata["engine"] = d["engine"]

                # 2. Legacy / Registry Fallback
                elif model:
                    metadata = {
                        "image": model["artifact_uri"],
                        "cmd": [
                            "meta-llama/Llama-2-7b-chat-hf",  # Generic placeholder if not in config
                            "--port",
                            "9000",
                        ],
                        "gpu": True,
                        "expose": [{"port": 9000, "type": "http"}],
                    }

                # 3. Last Resort / Error Check
                if (
                    not metadata.get("image")
                    and not metadata.get("cmd")
                    and metadata.get("workload_type") != "training"
                ):
                    # If we still lack info, we can't provision
                    log.error(f"Missing job definition for deployment {deployment_id}")
                    await self.deployments.update_state(
                        deployment_id,
                        "FAILED",
                        error_message="Missing job definition or image for deployment",
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
                    # No real compute job exists - simulation mode
                    await self.deployments.attach_runtime(
                        deployment_id=deployment_id,
                        allocation_ids=[],
                        node_ids=[],
                        runtime=f"{pool['provider']}-sim",
                    )
                    await self.deployments.update_state(deployment_id, "RUNNING")
                    return

                # ---- Universal Readiness Poll ----
                # Use adapter-specific timeout from capabilities
                timeout = capabilities.readiness_timeout_seconds
                expose_url = await adapter.wait_for_ready(
                    provider_instance_id=node_spec["provider_instance_id"],
                    timeout=timeout,
                    provider_credential_name=pool.get("provider_credential_name"),
                )

                # SAFETY CHECK: Verify that the deployment hasn't been terminated while we were waiting
                d_latest = await self.deployments.get(deployment_id)
                if not d_latest or d_latest["state"] != "PROVISIONING":
                    log.warning(
                        f"Deployment {deployment_id} state changed from PROVISIONING to {d_latest.get('state') if d_latest else 'None'} while waiting for provider. Aborting node registration."
                    )
                    # We should ideally deprovision if it was a real node,
                    # but termination handler might have already handled it or will handle it.
                    return

                # If the adapter returned a special indicator instead of a real URL,
                # check if the node_spec already had one (common for Akash/AWS)
                if not expose_url or expose_url.endswith("-ready"):
                    expose_url = expose_url or node_spec.get("expose_url")

                # Use URL directly from adapter if available (e.g. Akash, AWS)
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
                    provider_resource_id=None,  # Fix: Avoid passing string "image_uri" to UUID field
                    hostname=node_spec["hostname"],
                    gpu_total=node_spec["gpu_total"],
                    vcpu_total=node_spec["vcpu_total"],
                    ram_gb_total=node_spec["ram_gb_total"],
                    state="ready",
                    node_class=node_spec["node_class"],
                    metadata=node_spec["metadata"],
                    expose_url=expose_url,
                )

                # Attach node to deployment so terminate handler can find it
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

                # For ephemeral providers (DePIN, spot instances), deployment is complete once provisioned
                if capabilities.is_ephemeral:
                    return

            # -------- PLACEMENT --------
            if not candidates:
                log.error(
                    f"Insufficient capacity for deployment {deployment_id} after {MAX_PROVISION_RETRIES} provisioning attempts. Needs GPU={d['gpu_per_replica']}, vCPU={vcpu_req}, RAM={ram_gb_req}"
                )
                await self.deployments.update_state(
                    deployment_id,
                    "FAILED",
                    error_message=f"Insufficient capacity: GPU={d['gpu_per_replica']}, vCPU={vcpu_req}, RAM={ram_gb_req}",
                )
                return

            best_node = min(candidates, key=score_node)
            node_id = UUID(str(best_node["node_id"]))

            await self.deployments.update_state(deployment_id, "SCHEDULING")

            try:
                await self.deployments.update_state(deployment_id, "DEPLOYING")

                runtime = self.runtime_resolver.resolve(
                    replicas=d["replicas"],
                    gpu_per_replica=d["gpu_per_replica"],
                )

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
                    workload_type=None,  # d["workload_type"],
                )

            except Exception:
                await self.deployments.update_state(
                    deployment_id,
                    "FAILED",
                    error_message=f"Strategy deployment error: {e}",
                )
                raise

            # Normalize strategy outputs to UUID list shape expected by repository
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
                # **result,
            )

            await self.deployments.update_state(deployment_id, "RUNNING")

        except Exception as e:
            log.error(f"Unhandled error during provisioning for {deployment_id}: {e}")

            # Cleanup: if we provisioned a node but failed afterwards (e.g. DB error),
            # deprovision the cloud resources to avoid orphaned VMs.
            if node_spec and node_spec.get("provider_instance_id"):
                try:
                    cleanup_adapter = get_adapter(pool["provider"])
                    log.info(
                        f"Cleaning up orphaned node {node_spec['provider_instance_id']} "
                        f"after provisioning failure for {deployment_id}"
                    )
                    await cleanup_adapter.deprovision_node(
                        provider_instance_id=node_spec["provider_instance_id"],
                        provider_credential_name=pool.get("provider_credential_name"),
                    )
                except Exception as cleanup_err:
                    log.warning(
                        f"Failed to cleanup orphaned node for {deployment_id}: {cleanup_err}"
                    )

            # Only mark as FAILED if the deployment hasn't moved to a terminal state
            # (e.g. terminate handler may have already set STOPPED/TERMINATED)
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
            raise e

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

        # ------------------------------------
        # 4. FINAL STATE
        # ------------------------------------
        await self.deployments.update_state(deployment_id, "STOPPED")

        log.info(f"Deployment {deployment_id} stopped")
