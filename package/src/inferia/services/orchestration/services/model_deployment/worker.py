import uuid
import asyncio
import logging
from uuid import UUID

from inferia.services.orchestration.services.placement_engine.scoring import score_node
from inferia.services.orchestration.services.adapter_engine.registry import get_adapter

log = logging.getLogger(__name__)

MAX_PROVISION_RETRIES = 4
PROVISION_WAIT_SECONDS = 40
NOSANA_READY_TIMEOUT = 300  # seconds


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
        log.info(
            f"Handling deploy request for {deployment_id}. State: {d.get('state') if d else 'None'}"
        )
        if not d or d["state"] != "PENDING":
            log.warning(
                f"Skipping deploy for {deployment_id} because state is not PENDING"
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

        try:
            await self.deployments.update_state(deployment_id, "PROVISIONING")

            # Determine resource needs (default to full node if not specified, or hardcoded fallback)
            vcpu_req = resources_required["vcpu_total"] if resources_required else 8
            ram_gb_req = (
                resources_required["ram_gb_total"] if resources_required else 32
            )

            # -------- CAPACITY LOOP --------
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
                    await self.deployments.update_state(deployment_id, "FAILED")
                    return

                adapter = get_adapter(pool["provider"])

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
                            "--model",
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
                    await self.deployments.update_state(deployment_id, "FAILED")
                    return

                node_spec = await adapter.provision_node(
                    provider_resource_id=pool["allowed_gpu_types"][0],
                    pool_id=pool["provider_pool_id"],
                    metadata=metadata,
                )

                if node_spec.get("metadata", {}).get("mode") == "simulation":
                    # No real Nosana job exists
                    await self.deployments.attach_runtime(
                        deployment_id=deployment_id,
                        allocation_ids=[],
                        node_ids=[],
                        runtime="nosana-sim",
                    )
                    await self.deployments.update_state(deployment_id, "RUNNING")
                    return

                # ---- Universal Readiness Poll ----
                expose_url = await adapter.wait_for_ready(
                    provider_instance_id=node_spec["provider_instance_id"],
                    timeout=NOSANA_READY_TIMEOUT,
                )

                # SAFETY CHECK: Verify that the deployment hasn't been terminated while we were waiting
                d_latest = await self.deployments.get(deployment_id)
                if not d_latest or d_latest["state"] != "PROVISIONING":
                    log.warning(f"Deployment {deployment_id} state changed from PROVISIONING to {d_latest.get('state') if d_latest else 'None'} while waiting for provider. Aborting node registration.")
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

                await self.deployments.update_state(deployment_id, "RUNNING")

                # Nosana deployments are complete once the node is provisioned and registered.
                # Attach the node_id so terminate handler can find the job to stop.
                if pool["provider"] == "nosana":
                    if node_id:
                        await self.deployments.attach_runtime(
                            deployment_id=deployment_id,
                            allocation_ids=[],
                            node_ids=[node_id],
                            runtime="nosana",
                        )
                        log.info(
                            f"Nosana deployment {deployment_id} is RUNNING. Attached node_id {node_id}."
                        )
                    else:
                        log.warning(
                            f"Nosana deployment {deployment_id} is RUNNING but no node_id returned from register_node."
                        )
                    return

            # -------- PLACEMENT --------
            if not candidates:
                log.error(
                    f"Insufficient capacity for deployment {deployment_id} after {MAX_PROVISION_RETRIES} provisioning attempts. Needs GPU={d['gpu_per_replica']}, vCPU={vcpu_req}, RAM={ram_gb_req}"
                )
                await self.deployments.update_state(deployment_id, "FAILED")
                return

            best_node = min(candidates, key=score_node)
            node_id = UUID(str(best_node["node_id"]))

            await self.deployments.update_state(deployment_id, "SCHEDULING")

            allocation_ids = []

            try:
                for _ in range(d["replicas"]):
                    alloc_id = uuid.uuid4()
                    ok, reason, _ = await self.scheduler.allocate(
                        allocation_id=alloc_id,
                        node_id=node_id,
                        gpu=d["gpu_per_replica"],
                        vcpu=vcpu_req,
                        ram_gb=ram_gb_req,
                        priority=100,
                        owner_type="deployment",
                        owner_id=str(deployment_id),
                    )
                    if not ok:
                        raise RuntimeError(f"{reason} (Node: {node_id})")
                    allocation_ids.append(alloc_id)

                await self.deployments.update_state(deployment_id, "DEPLOYING")

                runtime = self.runtime_resolver.resolve(
                    replicas=d["replicas"],
                    gpu_per_replica=d["gpu_per_replica"],
                )

                strategy = self.strategies[runtime]
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
                for alloc in allocation_ids:
                    await self.scheduler.release(allocation_id=alloc)

                await self.deployments.update_state(deployment_id, "FAILED")
                raise

            await self.deployments.attach_runtime(
                deployment_id=deployment_id,
                allocation_ids=[result["allocation_ids"]],
                node_ids=[result["node_ids"]],
                runtime=result["runtime"],
                # **result,
            )

            await self.deployments.update_state(deployment_id, "RUNNING")

        except Exception as e:
            log.error(f"Unhandled error during provisioning for {deployment_id}: {e}")
            await self.deployments.update_state(deployment_id, "FAILED")
            raise e

    async def handle_terminate_requested(self, deployment_id: UUID):
        d = await self.deployments.get(deployment_id)
        if not d:
            return

        if d["state"] != "TERMINATING":
            return

        # ------------------------------------
        # 1. STOP RUNTIME (Nosana / vLLM / etc)
        # ------------------------------------
        # Use node_ids to find the exact running instances to stop
        if d.get("node_ids"):
            for node_id in d["node_ids"]:
                node = await self.inventory.get_node_by_id(node_id)
                if node:
                    adapter = get_adapter(node["provider"])
                    log.info(f"Deprovisioning {node['provider']} node {node_id}")
                    await adapter.deprovision_node(
                        provider_instance_id=node["provider_instance_id"]
                    )

        print(f"Stopped runtime for deployment {deployment_id}")

        # ------------------------------------
        # 2. RELEASE SCHEDULER ALLOCATIONS
        # ------------------------------------
        if d.get("allocation_ids"):
            for alloc_id in d["allocation_ids"]:
                await self.scheduler.release(allocation_id=alloc_id)

        print(f"Released scheduler allocations for deployment {deployment_id}")

        # ------------------------------------
        # 3. RECYCLE INVENTORY
        # ------------------------------------
        # ------------------------------------
        # 3. HANDLE INVENTORY
        # ------------------------------------
        if d.get("node_ids"):
            for node_id in d["node_ids"]:
                # Logic: If ephemeral (Nosana), mark terminated so we provision fresh next time.
                # If static (On-prem), recycle to 'ready' to release back to pool.

                is_ephemeral = False
                if node and node.get("provider") == "nosana":
                    is_ephemeral = True

                if is_ephemeral:
                    await self.inventory.mark_terminated(node_id)
                    print(f"Terminated ephemeral node {node_id}")
                else:
                    await self.inventory.recycle_node(node_id)
                    print(f"Recycled inventory node {node_id}")

        # ------------------------------------
        # 4. FINAL STATE
        # ------------------------------------
        await self.deployments.update_state(deployment_id, "STOPPED")

        print(f"Deployment {deployment_id} stopped")
