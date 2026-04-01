# Deployment Failure Retry Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically retry failed deployments (all providers) with exponential backoff, same-node-first strategy, configurable limit, and a visible `RETRYING` state in the dashboard.

**Architecture:** Add a retry loop in `worker.py`'s `handle_deploy_requested()` wrapping the provision → wait → deploy section. On failure, first retry on the same node, then re-provision fresh nodes. Track retry metadata in the deployment's `configuration` JSONB. Expose `RETRYING` state in the dashboard with amber badge.

**Tech Stack:** Python (asyncio), PostgreSQL JSONB, React/TypeScript

---

## File Structure

| File | Responsibility |
|------|---------------|
| **Modify:** `package/src/inferia/services/orchestration/config.py` | Add `max_deployment_retries` setting |
| **Modify:** `package/src/inferia/services/orchestration/repositories/model_deployment_repo.py` | Allow `RETRYING` state to preserve error_message, add `update_configuration()` method |
| **Modify:** `package/src/inferia/services/orchestration/services/model_deployment/worker.py` | Add retry loop with exponential backoff and same-node-first logic |
| **Modify:** `apps/dashboard/src/pages/Deployments.tsx` | Add `RETRYING` status badge styling and action control |
| **Modify:** `apps/dashboard/src/pages/DeploymentDetail.tsx` | Add `RETRYING` status rendering in detail header |
| **Modify:** `apps/dashboard/src/components/deployment/DeploymentOverview.tsx` | Add `RETRYING` state icon and color |
| **Create:** `package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py` | Tests for retry behavior |

---

### Task 1: Add config setting for max deployment retries

**Files:**
- Modify: `package/src/inferia/services/orchestration/config.py`

- [ ] **Step 1: Add the config field**

In `package/src/inferia/services/orchestration/config.py`, add after the `default_polling_interval` field (line 115):

```python
    # Deployment Retry Logic
    max_deployment_retries: int = Field(
        default=2, validation_alias="MAX_DEPLOYMENT_RETRIES"
    )
```

- [ ] **Step 2: Verify config loads**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -c "from inferia.services.orchestration.config import Settings; s = Settings(_env_file=None, postgres_dsn='postgresql://test:test@localhost/test'); print(s.max_deployment_retries)"`
Expected: `2`

- [ ] **Step 3: Commit**

```bash
git add package/src/inferia/services/orchestration/config.py
git commit -m "feat: add max_deployment_retries config setting (#167)"
```

---

### Task 2: Update repository to support RETRYING state

**Files:**
- Modify: `package/src/inferia/services/orchestration/repositories/model_deployment_repo.py`
- Create: `package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py`

- [ ] **Step 1: Write the failing test**

Create `package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py`:

```python
"""Tests for deployment retry logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class TestRepoRetryingState:
    """Test that RETRYING state preserves error_message in update_state."""

    @pytest.mark.asyncio
    async def test_retrying_state_preserves_error_message(self):
        """RETRYING state should NOT clear error_message like other non-FAILED states do."""
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db = AsyncMock()
        mock_conn = AsyncMock()
        mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock()

        repo = ModelDeploymentRepository(mock_db, mock_bus)
        dep_id = uuid4()

        await repo.update_state(
            dep_id, "RETRYING", error_message="Retry 1/2: CUDA error"
        )

        # Verify the error_message was NOT cleared
        call_args = mock_conn.execute.call_args
        assert call_args[0][2] == "RETRYING"
        assert call_args[0][3] == "Retry 1/2: CUDA error"

    @pytest.mark.asyncio
    async def test_non_failure_state_clears_error_message(self):
        """Non-FAILED/non-RETRYING states should clear error_message."""
        from inferia.services.orchestration.repositories.model_deployment_repo import (
            ModelDeploymentRepository,
        )

        mock_db = AsyncMock()
        mock_conn = AsyncMock()
        mock_db.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_bus = AsyncMock()
        mock_bus.publish = AsyncMock()

        repo = ModelDeploymentRepository(mock_db, mock_bus)
        dep_id = uuid4()

        await repo.update_state(
            dep_id, "RUNNING", error_message="should be cleared"
        )

        call_args = mock_conn.execute.call_args
        assert call_args[0][2] == "RUNNING"
        assert call_args[0][3] is None  # error_message cleared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py::TestRepoRetryingState -v`
Expected: FAIL — `test_retrying_state_preserves_error_message` fails because `RETRYING` is not `FAILED`, so error_message gets cleared

- [ ] **Step 3: Update update_state to preserve error_message for RETRYING**

In `package/src/inferia/services/orchestration/repositories/model_deployment_repo.py`, change line 127-128 from:

```python
        if state != "FAILED":
            error_message = None
```

to:

```python
        if state not in ("FAILED", "RETRYING"):
            error_message = None
```

- [ ] **Step 4: Apply same fix to update_state_if**

In the same file, change line 162-163 from:

```python
        if new_state != "FAILED":
            error_message = None
```

to:

```python
        if new_state not in ("FAILED", "RETRYING"):
            error_message = None
```

- [ ] **Step 5: Add update_configuration method**

Add at the end of the `ModelDeploymentRepository` class (after the `delete` method):

```python
    async def update_configuration(self, deployment_id: UUID, configuration: dict):
        """Update the configuration JSONB field for a deployment."""
        import json

        q = """
        UPDATE model_deployments
        SET configuration=$2, updated_at=now()
        WHERE deployment_id=$1
        """
        config_json = json.dumps(configuration)
        async with self.db.acquire() as c:
            await c.execute(q, deployment_id, config_json)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py::TestRepoRetryingState -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add package/src/inferia/services/orchestration/repositories/model_deployment_repo.py package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py
git commit -m "feat: support RETRYING state in deployment repo (#167)"
```

---

### Task 3: Add retry loop to worker

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/worker.py`
- Modify: `package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py`

- [ ] **Step 1: Write the failing tests for retry behavior**

Append to `test_retry_logic.py`:

```python
import asyncio
from uuid import UUID


class TestWorkerRetryLogic:
    """Test deployment retry behavior in the worker."""

    def _make_worker(self, deployment_repo=None, pool_repo=None, inventory_repo=None):
        """Create a ModelDeploymentWorker with mocked dependencies."""
        from inferia.services.orchestration.services.model_deployment.worker import (
            ModelDeploymentWorker,
        )

        return ModelDeploymentWorker(
            deployment_repo=deployment_repo or AsyncMock(),
            model_registry_repo=AsyncMock(),
            pool_repo=pool_repo or AsyncMock(),
            placement_repo=AsyncMock(),
            scheduler=AsyncMock(),
            inventory_repo=inventory_repo or AsyncMock(),
            runtime_resolver=AsyncMock(),
            runtime_strategies={},
        )

    @pytest.mark.asyncio
    async def test_retry_on_provision_failure_marks_retrying(self):
        """When provision fails and retries remain, state should go to RETRYING."""
        dep_id = uuid4()
        deployment = {
            "deployment_id": dep_id,
            "state": "PENDING",
            "pool_id": uuid4(),
            "model_id": None,
            "model_name": "test-model",
            "engine": "vllm",
            "gpu_per_replica": 1,
            "replicas": 1,
            "configuration": {"image": "test:latest", "cmd": ["serve"]},
            "inference_model": None,
            "model_type": "inference",
        }
        pool = {
            "provider": "nosana",
            "provider_pool_id": "pool-1",
            "allowed_gpu_types": ["A100"],
            "pool_type": "job",
            "cluster_id": None,
            "provider_credential_name": "cred-1",
        }

        dep_repo = AsyncMock()
        dep_repo.get = AsyncMock(side_effect=[deployment, deployment, deployment, deployment])
        dep_repo.update_state_if = AsyncMock(return_value=True)
        dep_repo.update_state = AsyncMock()
        dep_repo.update_configuration = AsyncMock()

        pool_repo = AsyncMock()
        pool_repo.get = AsyncMock(return_value=pool)

        inventory_repo = AsyncMock()
        inventory_repo.get_resource_requirement = AsyncMock(return_value={"vcpu_total": 8, "ram_gb_total": 32})

        worker = self._make_worker(
            deployment_repo=dep_repo,
            pool_repo=pool_repo,
            inventory_repo=inventory_repo,
        )

        # Mock adapter to fail on provision
        mock_adapter = AsyncMock()
        mock_adapter.get_capabilities.return_value = MagicMock(
            supports_cluster_mode=False,
            is_ephemeral=True,
            requires_readiness_poll=True,
            readiness_timeout_seconds=300,
        )
        mock_adapter.provision_node = AsyncMock(
            side_effect=RuntimeError("CUDA incompatible")
        )
        mock_adapter.deprovision_node = AsyncMock()

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            with patch(
                "inferia.services.orchestration.services.model_deployment.worker.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                # Should not raise — retries should be exhausted and mark FAILED
                try:
                    await worker.handle_deploy_requested(dep_id)
                except Exception:
                    pass

        # Verify RETRYING state was set at some point
        retrying_calls = [
            c for c in dep_repo.update_state.call_args_list
            if len(c[0]) >= 2 and c[0][1] == "RETRYING"
        ]
        assert len(retrying_calls) > 0, "Should have set RETRYING state during retries"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_marks_failed(self):
        """When all retries are exhausted, deployment should be FAILED."""
        dep_id = uuid4()
        deployment = {
            "deployment_id": dep_id,
            "state": "PENDING",
            "pool_id": uuid4(),
            "model_id": None,
            "model_name": "test-model",
            "engine": "vllm",
            "gpu_per_replica": 1,
            "replicas": 1,
            "configuration": {"image": "test:latest", "cmd": ["serve"]},
            "inference_model": None,
            "model_type": "inference",
        }
        pool = {
            "provider": "nosana",
            "provider_pool_id": "pool-1",
            "allowed_gpu_types": ["A100"],
            "pool_type": "job",
            "cluster_id": None,
            "provider_credential_name": "cred-1",
        }

        dep_repo = AsyncMock()
        dep_repo.get = AsyncMock(return_value=deployment)
        dep_repo.update_state_if = AsyncMock(return_value=True)
        dep_repo.update_state = AsyncMock()
        dep_repo.update_configuration = AsyncMock()

        pool_repo = AsyncMock()
        pool_repo.get = AsyncMock(return_value=pool)

        inventory_repo = AsyncMock()
        inventory_repo.get_resource_requirement = AsyncMock(return_value={"vcpu_total": 8, "ram_gb_total": 32})

        worker = self._make_worker(
            deployment_repo=dep_repo,
            pool_repo=pool_repo,
            inventory_repo=inventory_repo,
        )

        mock_adapter = AsyncMock()
        mock_adapter.get_capabilities.return_value = MagicMock(
            supports_cluster_mode=False,
            is_ephemeral=True,
            requires_readiness_poll=True,
            readiness_timeout_seconds=300,
        )
        mock_adapter.provision_node = AsyncMock(
            side_effect=RuntimeError("CUDA incompatible")
        )
        mock_adapter.deprovision_node = AsyncMock()

        with patch(
            "inferia.services.orchestration.services.model_deployment.worker.get_adapter",
            return_value=mock_adapter,
        ):
            with patch(
                "inferia.services.orchestration.services.model_deployment.worker.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                with patch(
                    "inferia.services.orchestration.services.model_deployment.worker.settings",
                ) as mock_settings:
                    mock_settings.max_deployment_retries = 2
                    try:
                        await worker.handle_deploy_requested(dep_id)
                    except Exception:
                        pass

        # Last update_state call should be FAILED
        failed_calls = [
            c for c in dep_repo.update_state.call_args_list
            if len(c[0]) >= 2 and c[0][1] == "FAILED"
        ]
        assert len(failed_calls) > 0, "Should have marked deployment as FAILED after retries exhausted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py::TestWorkerRetryLogic -v`
Expected: FAIL — no retry logic in worker yet

- [ ] **Step 3: Import settings in worker.py**

At the top of `package/src/inferia/services/orchestration/services/model_deployment/worker.py`, after the existing imports (line 10), add:

```python
from inferia.services.orchestration.config import settings
```

- [ ] **Step 4: Add retry constants**

After `PROVISION_WAIT_SECONDS = 40` (line 14), add:

```python
RETRY_BASE_DELAY_SECONDS = 10
```

- [ ] **Step 5: Wrap the job-based deployment section in a retry loop**

In `handle_deploy_requested()`, replace the job-based deployment section (lines 246-534, the `node_spec = None` through the end of the outer `except` block) with the retry-wrapped version.

The key change: wrap everything from `node_spec = None` (line 246) through the end of the `except Exception as e:` block (line 534) in a retry `for` loop.

Replace lines 246-534 with:

```python
        # ------------------------------------
        # JOB-BASED DEPLOYMENT (Nosana, Akash) or PLACEMENT
        # with retry logic
        # ------------------------------------

        max_retries = settings.max_deployment_retries
        last_error = None
        node_spec = None

        for attempt in range(max_retries + 1):  # 0 = initial, 1..max_retries = retries
            try:
                if attempt > 0:
                    # --- RETRY PATH ---
                    backoff = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    log.info(
                        f"Retry {attempt}/{max_retries} for deployment {deployment_id} "
                        f"after {backoff}s backoff. Last error: {last_error}"
                    )
                    await self.deployments.update_state(
                        deployment_id,
                        "RETRYING",
                        error_message=f"Retry {attempt}/{max_retries}: {last_error}",
                    )

                    # Save retry metadata in configuration
                    config = d.get("configuration") or {}
                    if isinstance(config, str):
                        import json
                        try:
                            config = json.loads(config)
                        except Exception:
                            config = {}
                    config["retry_count"] = attempt
                    config["max_retries"] = max_retries
                    config["last_retry_error"] = str(last_error)
                    await self.deployments.update_configuration(deployment_id, config)

                    await asyncio.sleep(backoff)

                    # CAS check: bail if deployment was cancelled during backoff
                    d_check = await self.deployments.get(deployment_id)
                    if not d_check or d_check["state"] not in ("RETRYING", "PROVISIONING"):
                        log.info(
                            f"Deployment {deployment_id} state changed to "
                            f"{d_check['state'] if d_check else 'deleted'} during retry backoff, aborting."
                        )
                        return

                    await self.deployments.update_state(deployment_id, "PROVISIONING")

                    # Cleanup previous node if exists (attempt > 0 means prior failure)
                    if node_spec and node_spec.get("provider_instance_id"):
                        try:
                            cleanup_adapter = get_adapter(pool["provider"])
                            log.info(
                                f"Cleaning up node {node_spec['provider_instance_id']} before retry"
                            )
                            await cleanup_adapter.deprovision_node(
                                provider_instance_id=node_spec["provider_instance_id"],
                                provider_credential_name=pool.get("provider_credential_name"),
                            )
                        except Exception as cleanup_err:
                            log.warning(f"Failed to cleanup node before retry: {cleanup_err}")
                    node_spec = None

                # Determine resource needs
                vcpu_req = resources_required["vcpu_total"] if resources_required else 8
                ram_gb_req = (
                    resources_required["ram_gb_total"] if resources_required else 32
                )

                # -------- CAPACITY LOOP --------
                candidates = []
                for prov_attempt in range(MAX_PROVISION_RETRIES + 1):
                    candidates = await self.placement.fetch_candidate_nodes(
                        pool_id=d["pool_id"],
                        gpu_req=d["gpu_per_replica"],
                        vcpu_req=vcpu_req,
                        ram_req=ram_gb_req,
                    )

                    if candidates:
                        break

                    if prov_attempt == MAX_PROVISION_RETRIES:
                        if attempt == max_retries:
                            # Final attempt, no candidates — mark FAILED
                            await self.deployments.update_state(
                                deployment_id,
                                "FAILED",
                                error_message=f"No available nodes after {MAX_PROVISION_RETRIES} provisioning attempts",
                            )
                            return
                        else:
                            # More retries available, raise to trigger retry
                            raise RuntimeError(
                                f"No available nodes after {MAX_PROVISION_RETRIES} provisioning attempts"
                            )

                    adapter = get_adapter(pool["provider"])
                    capabilities = adapter.get_capabilities()

                    # Determine Metadata / Job Spec
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

                    elif model:
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

                    if (
                        not metadata.get("image")
                        and not metadata.get("cmd")
                        and metadata.get("workload_type") != "training"
                    ):
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

                    # Handle simulation mode
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

                    # SAFETY CHECK
                    d_latest = await self.deployments.get(deployment_id)
                    if not d_latest or d_latest["state"] not in ("PROVISIONING", "RETRYING"):
                        log.warning(
                            f"Deployment {deployment_id} state changed to "
                            f"{d_latest.get('state') if d_latest else 'None'} during provisioning. Aborting."
                        )
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

                await self.deployments.update_state(deployment_id, "SCHEDULING")

                try:
                    await self.deployments.update_state(deployment_id, "DEPLOYING")

                    runtime = self.runtime_resolver.resolve(
                        replicas=d["replicas"],
                        gpu_per_replica=d["gpu_per_replica"],
                        engine=d.get("engine"),
                        model_type=d.get("model_type"),
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
                        workload_type=None,
                    )

                except Exception as e:
                    await self.deployments.update_state(
                        deployment_id,
                        "FAILED",
                        error_message=f"Strategy deployment error: {e}",
                    )
                    raise

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
                return  # Success — exit retry loop

            except Exception as e:
                last_error = e
                log.warning(
                    f"Attempt {attempt}/{max_retries} failed for deployment {deployment_id}: {e}"
                )

                if attempt < max_retries:
                    # More retries available — continue loop
                    continue

                # All retries exhausted — cleanup and mark FAILED
                log.error(f"All retries exhausted for deployment {deployment_id}: {e}")

                if node_spec and node_spec.get("provider_instance_id"):
                    try:
                        cleanup_adapter = get_adapter(pool["provider"])
                        log.info(
                            f"Cleaning up orphaned node {node_spec['provider_instance_id']} "
                            f"after final retry failure for {deployment_id}"
                        )
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
                        error_message=f"Failed after {max_retries} retries: {e}",
                    )
                else:
                    log.info(
                        f"Skipping FAILED state update for {deployment_id} — "
                        f"already in terminal state: {d_current['state'] if d_current else 'deleted'}"
                    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run all model_deployment tests for regressions**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/ -v`
Expected: All existing tests pass

- [ ] **Step 8: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/worker.py package/src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py
git commit -m "feat: add retry loop with exponential backoff to deployment worker (#167)"
```

---

### Task 4: Update terminate handler to support RETRYING state

**Files:**
- Modify: `package/src/inferia/services/orchestration/services/model_deployment/worker.py`

- [ ] **Step 1: Allow terminate handler to handle RETRYING deployments**

In `handle_terminate_requested()`, change line 541 from:

```python
        if d["state"] != "TERMINATING":
            return
```

to:

```python
        if d["state"] not in ("TERMINATING",):
            return
```

This remains the same — the controller's `request_delete()` already transitions to `TERMINATING` before publishing the terminate event. Since `RETRYING` deployments would first be set to `TERMINATING` by the controller, this works as-is.

However, we should also ensure the controller's `request_delete` accepts `RETRYING` as a valid source state. Check `controller.py`.

- [ ] **Step 2: Verify controller handles RETRYING for deletion**

Read `package/src/inferia/services/orchestration/services/model_deployment/controller.py` and find `request_delete`. If it checks for valid states, add `RETRYING` to the allowed list.

In the controller's `request_delete` method, look for state checks. The method calls `update_state_if` with `expected_state`. If it only accepts specific states, add `RETRYING`. If it accepts any non-terminal state, no change needed.

Based on the exploration, `request_delete` transitions to `TERMINATING`. If it uses `update_state` (not `update_state_if`), it works for any state. If it uses `update_state_if`, ensure `RETRYING` is accepted.

- [ ] **Step 3: Commit**

```bash
git add package/src/inferia/services/orchestration/services/model_deployment/worker.py package/src/inferia/services/orchestration/services/model_deployment/controller.py
git commit -m "feat: support RETRYING state in terminate and delete flows (#167)"
```

---

### Task 5: Dashboard — add RETRYING status badge

**Files:**
- Modify: `apps/dashboard/src/pages/Deployments.tsx`
- Modify: `apps/dashboard/src/pages/DeploymentDetail.tsx`
- Modify: `apps/dashboard/src/components/deployment/DeploymentOverview.tsx`

- [ ] **Step 1: Add RETRYING to Deployments.tsx status functions**

In `apps/dashboard/src/pages/Deployments.tsx`, update `getStatusStyles` (around line 60):

```typescript
function getStatusStyles(status: string) {
  if (status === "READY" || status === "RUNNING") {
    return "border-green-200 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800";
  }
  if (status === "STOPPED" || status === "TERMINATED") {
    return "border-slate-200 bg-slate-50 text-slate-700 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700";
  }
  if (status === "FAILED") {
    return "border-red-200 bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800";
  }
  if (status === "RETRYING") {
    return "border-amber-200 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-800";
  }
  return "border-yellow-200 bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800";
}
```

Update `getStatusDot` (around line 73):

```typescript
function getStatusDot(status: string) {
  if (status === "READY" || status === "RUNNING") return "bg-green-500";
  if (status === "STOPPED" || status === "TERMINATED") return "bg-zinc-400";
  if (status === "FAILED") return "bg-red-500";
  if (status === "RETRYING") return "bg-amber-500 animate-pulse";
  return "bg-yellow-500";
}
```

Update action control logic (around line 471) — `RETRYING` deployments should be treated as running (not deletable/startable):

```typescript
const isRunning = ["READY", "RUNNING", "PENDING", "DEPLOYING", "RETRYING"].includes(deployment.status);
const canStart = ["STOPPED", "TERMINATED", "FAILED"].includes(deployment.status);
const canDelete = ["STOPPED", "TERMINATED", "FAILED"].includes(deployment.status);
```

- [ ] **Step 2: Add RETRYING to DeploymentDetail.tsx**

In `apps/dashboard/src/pages/DeploymentDetail.tsx`, find the status badge rendering (around line 254). The current logic is binary (isRunning vs not). Update the `isRunning` check to include `RETRYING`:

Find where `isRunning` is defined and add `RETRYING`:

```typescript
const isRunning = state === "RUNNING" || state === "READY" || state === "RETRYING";
```

Or if the status badge uses inline checks, add a `RETRYING` case with amber styling.

- [ ] **Step 3: Add RETRYING to DeploymentOverview.tsx**

In `apps/dashboard/src/components/deployment/DeploymentOverview.tsx`, update the status display (around line 130):

Add a `RETRYING` case before the default:

```typescript
state === "RETRYING" ? "text-amber-600 dark:text-amber-500" :
```

And for the icon:

```typescript
state === "RETRYING" ? <RefreshCcw className="w-5 h-5 animate-spin" /> :
```

Make sure `RefreshCcw` is imported from `lucide-react` at the top of the file.

- [ ] **Step 4: Verify dashboard builds**

Run: `cd /storage/intern/hooman/InferiaLLM/apps/dashboard && npm run build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/src/pages/Deployments.tsx apps/dashboard/src/pages/DeploymentDetail.tsx apps/dashboard/src/components/deployment/DeploymentOverview.tsx
git commit -m "feat: add RETRYING status badge to dashboard (#167)"
```

---

### Task 6: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all retry tests**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/test_retry_logic.py -v`
Expected: All 4 tests pass

- [ ] **Step 2: Run all model_deployment tests**

Run: `cd /storage/intern/hooman/InferiaLLM/package && .venv/bin/python -m pytest src/inferia/services/orchestration/test/model_deployment/ -v`
Expected: All tests pass (no regressions)

- [ ] **Step 3: Run dashboard build**

Run: `cd /storage/intern/hooman/InferiaLLM/apps/dashboard && npm run build`
Expected: Clean build

- [ ] **Step 4: Commit if any fixes needed**

```bash
git commit -m "fix: address test/build issues in retry implementation (#167)"
```
