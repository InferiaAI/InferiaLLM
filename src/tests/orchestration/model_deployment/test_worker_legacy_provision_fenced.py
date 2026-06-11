"""Unit tests for the legacy-worker provision_node fence (Task 1.4).

The ModelDeploymentWorker.handle_deploy_requested capacity loop must NOT call
adapter.provision_node for reconciler-managed providers (aws, gcp, azure,
on_prem, worker).  Instead it must mark the deployment FAILED with a clear,
actionable message and return cleanly — no NotImplementedError leaking to the
outer handler.

See worker.py, the _RECONCILER_MANAGED_PROVIDERS fence added in Task 1.4.

IMPORTANT (see MEMORY: AsyncMock signature blindness): every repo mock is
pinned with spec=RealClass to catch signature drift.  We also assert on
await_args.kwargs for the FAILED state update to verify the error_message.

Run with:
    docker exec inferia-test sh -lc 'cd /usr/local/lib/python3.12/site-packages/\
inferia/services/orchestration/services/model_deployment/tests && \
python -m pytest test_worker_legacy_provision_fenced.py -q'
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orchestration.models.model_deployment.worker import (
    ModelDeploymentWorker,
)
from orchestration.repositories.inventory_repo import (
    InventoryRepository,
)
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from orchestration.repositories.placement_repo import (
    PlacementRepository,
)
from orchestration.repositories.pool_repo import (
    ComputePoolRepository,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FENCING_PROVIDERS = ["aws", "gcp", "azure", "on_prem", "worker"]
_NON_FENCING_PROVIDERS = ["nosana", "akash"]


def _make_deployment(deployment_id, pool_id, *, provider="aws"):
    """Return a minimal deployment dict that passes the state checks."""
    return {
        "deployment_id": str(deployment_id),
        "pool_id": str(pool_id),
        "state": "PENDING",
        "model_id": None,
        "gpu_per_replica": 1,
        "replicas": 1,
        "engine": "vllm",
        "inference_model": "org/model",
        "model_name": "model",
        "model_type": None,
        "configuration": None,
    }


def _make_pool(pool_id, *, provider="aws"):
    return {
        "id": str(pool_id),
        "provider": provider,
        "provider_pool_id": "pool-001",
        "allowed_gpu_types": ["g6.xlarge"],
        "pool_type": "job",
        "cluster_id": None,
        "provider_credential_name": None,
    }


def _make_worker(
    deployment_id,
    pool_id,
    *,
    deployment_repo=None,
    pool_repo=None,
    placement_repo=None,
    inventory_repo=None,
) -> ModelDeploymentWorker:
    deployment_repo = deployment_repo or AsyncMock(spec=ModelDeploymentRepository)
    pool_repo = pool_repo or AsyncMock(spec=ComputePoolRepository)
    placement_repo = placement_repo or AsyncMock(spec=PlacementRepository)
    inventory_repo = inventory_repo or AsyncMock(spec=InventoryRepository)

    return ModelDeploymentWorker(
        deployment_repo=deployment_repo,
        model_registry_repo=AsyncMock(),
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        scheduler=AsyncMock(),
        inventory_repo=inventory_repo,
        runtime_resolver=AsyncMock(),
        runtime_strategies={},
    )


# ---------------------------------------------------------------------------
# Core fence tests: reconciler-managed providers must not call provision_node
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", _FENCING_PROVIDERS)
async def test_fenced_provider_marks_failed_without_calling_provision_node(provider):
    """All reconciler-managed providers (aws, gcp, azure, on_prem, worker) must:

    1. Mark the deployment FAILED with the clear fencing error_message.
    2. NEVER await adapter.provision_node.
    """
    deploy_id = uuid4()
    pool_id = uuid4()

    deployment = _make_deployment(deploy_id, pool_id, provider=provider)
    pool = _make_pool(pool_id, provider=provider)

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = deployment
    deploy_repo.update_state_if.return_value = True  # CAS succeeds → PROVISIONING

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = pool

    placement_repo = AsyncMock(spec=PlacementRepository)
    # No candidate nodes → triggers the provision path inside the loop
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None

    # Build an adapter mock with spec=None (concrete class not importable
    # in test context) but make provision_node an AsyncMock so we can assert
    # it was not awaited.
    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock()
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=False,
        readiness_timeout_seconds=300,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    # provision_node must NOT be awaited
    mock_adapter.provision_node.assert_not_awaited()

    # Deployment must have been marked FAILED with the fencing message
    deploy_repo.update_state.assert_awaited()
    # Find the FAILED update call
    failed_calls = [
        call
        for call in deploy_repo.update_state.await_args_list
        if len(call.args) >= 2 and call.args[1] == "FAILED"
    ]
    assert failed_calls, (
        f"Expected at least one update_state(_, 'FAILED') call for provider={provider!r}"
    )

    # Verify the error_message contains the key fencing text
    last_failed_call = failed_calls[-1]
    error_message = last_failed_call.kwargs.get(
        "error_message",
        last_failed_call.args[2] if len(last_failed_call.args) > 2 else None,
    )
    assert error_message is not None, "FAILED update must carry an error_message"
    assert provider in error_message, (
        f"error_message should name the provider; got: {error_message!r}"
    )
    assert "reconciler" in error_message.lower(), (
        f"error_message should mention 'reconciler'; got: {error_message!r}"
    )


async def test_aws_fenced_deployment_error_message_exact_format():
    """The fencing error_message for aws must mention the reconciler and /deploy."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = _make_deployment(deploy_id, pool_id, provider="aws")
    deploy_repo.update_state_if.return_value = True

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = _make_pool(pool_id, provider="aws")

    placement_repo = AsyncMock(spec=PlacementRepository)
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock()
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=False,
        readiness_timeout_seconds=300,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    failed_calls = [
        call
        for call in deploy_repo.update_state.await_args_list
        if len(call.args) >= 2 and call.args[1] == "FAILED"
    ]
    assert failed_calls, "Expected FAILED state update for AWS fencing"
    last_call = failed_calls[-1]
    error_message = last_call.kwargs.get(
        "error_message",
        last_call.args[2] if len(last_call.args) > 2 else None,
    )
    assert error_message is not None
    # Verify the message contains the three key pieces of information
    assert "aws" in error_message
    assert "reconciler" in error_message.lower()
    assert "/deploy" in error_message or "POST" in error_message


async def test_fenced_provider_returns_cleanly_no_exception_raised():
    """The fence must return cleanly — no NotImplementedError should propagate."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = _make_deployment(deploy_id, pool_id, provider="aws")
    deploy_repo.update_state_if.return_value = True

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = _make_pool(pool_id, provider="aws")

    placement_repo = AsyncMock(spec=PlacementRepository)
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None

    mock_adapter = MagicMock()
    # provision_node would raise NotImplementedError for real PulumiAWSAdapter
    mock_adapter.provision_node = AsyncMock(side_effect=NotImplementedError("T10"))
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=False,
        readiness_timeout_seconds=300,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        # Must not raise
        result = await worker.handle_deploy_requested(deploy_id)

    assert result is None  # returns cleanly
    # provision_node was never called even though its side_effect is set
    mock_adapter.provision_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# Non-fenced providers: legacy path must remain intact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", _NON_FENCING_PROVIDERS)
async def test_non_fenced_provider_calls_provision_node(provider):
    """Providers not in _RECONCILER_MANAGED_PROVIDERS (nosana, akash) must still
    go through adapter.provision_node — the legacy path must NOT be broken."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deployment = _make_deployment(deploy_id, pool_id, provider=provider)
    pool = dict(_make_pool(pool_id, provider=provider))

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.side_effect = [
        deployment,
        # Called once more for the SAFETY CHECK post-provision
        {**deployment, "state": "PROVISIONING"},
    ]
    deploy_repo.update_state_if.return_value = True

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = pool

    placement_repo = AsyncMock(spec=PlacementRepository)
    # No candidates on first attempt triggers provision, then candidates on
    # the next iteration would normally break out.  We make provision_node
    # succeed so we can assert it was called.
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None
    inventory_repo.register_node.return_value = uuid4()

    # Successful provision_node return value (DePIN-style)
    node_spec = {
        "provider": provider,
        "provider_instance_id": "inst-abc",
        "hostname": "node.example.com",
        "gpu_total": 1,
        "vcpu_total": 4,
        "ram_gb_total": 16,
        "node_class": "standard",
        "metadata": {"mode": ""},
        "expose_url": "http://node.example.com:8000",
    }

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock(return_value=node_spec)
    mock_adapter.wait_for_ready = AsyncMock(return_value="http://node.example.com:8000")
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=True,
        readiness_timeout_seconds=60,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    # For non-fenced providers, provision_node MUST have been called
    mock_adapter.provision_node.assert_awaited_once()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_fenced_deployment_not_reached_when_state_not_pending():
    """If state is not PENDING the worker returns early — provision_node is
    never reached regardless of provider."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deployment = _make_deployment(deploy_id, pool_id, provider="aws")
    deployment["state"] = "RUNNING"

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = deployment

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    placement_repo = AsyncMock(spec=PlacementRepository)
    inventory_repo = AsyncMock(spec=InventoryRepository)

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock()

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    mock_adapter.provision_node.assert_not_awaited()
    # No state update for a non-PENDING deployment
    deploy_repo.update_state.assert_not_awaited()


async def test_fenced_deployment_cas_fails_does_not_reach_provision():
    """If update_state_if (CAS PENDING→PROVISIONING) fails, the early return
    before the capacity loop means provision_node is never reached."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = _make_deployment(deploy_id, pool_id, provider="aws")
    deploy_repo.update_state_if.return_value = False  # CAS fails → early return

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    placement_repo = AsyncMock(spec=PlacementRepository)
    inventory_repo = AsyncMock(spec=InventoryRepository)

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock()

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    mock_adapter.provision_node.assert_not_awaited()


async def test_fenced_provider_fence_runs_on_first_retry_attempt():
    """The fence is inside the capacity loop.  Even on attempt 0 (first
    iteration) with no candidates, the fence fires before provision_node."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = _make_deployment(deploy_id, pool_id, provider="gcp")
    deploy_repo.update_state_if.return_value = True

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = _make_pool(pool_id, provider="gcp")

    placement_repo = AsyncMock(spec=PlacementRepository)
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock()
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=False,
        readiness_timeout_seconds=300,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        await worker.handle_deploy_requested(deploy_id)

    mock_adapter.provision_node.assert_not_awaited()

    failed_calls = [
        call
        for call in deploy_repo.update_state.await_args_list
        if len(call.args) >= 2 and call.args[1] == "FAILED"
    ]
    assert failed_calls, "Expected FAILED update for gcp provider"


async def test_on_prem_alias_is_also_fenced():
    """on_prem is a WorkerAdapter alias — provision_node raises NotImplementedError
    for WorkerAdapter too, so it must be fenced."""
    deploy_id = uuid4()
    pool_id = uuid4()

    deploy_repo = AsyncMock(spec=ModelDeploymentRepository)
    deploy_repo.get.return_value = _make_deployment(deploy_id, pool_id, provider="on_prem")
    deploy_repo.update_state_if.return_value = True

    pool_repo = AsyncMock(spec=ComputePoolRepository)
    pool_repo.get.return_value = _make_pool(pool_id, provider="on_prem")

    placement_repo = AsyncMock(spec=PlacementRepository)
    placement_repo.fetch_candidate_nodes.return_value = []

    inventory_repo = AsyncMock(spec=InventoryRepository)
    inventory_repo.get_resource_requirement.return_value = None

    mock_adapter = MagicMock()
    mock_adapter.provision_node = AsyncMock(
        side_effect=NotImplementedError("Worker pools self-provision")
    )
    mock_adapter.get_capabilities.return_value = MagicMock(
        supports_cluster_mode=False,
        is_ephemeral=False,
        readiness_timeout_seconds=300,
    )

    worker = _make_worker(
        deploy_id,
        pool_id,
        deployment_repo=deploy_repo,
        pool_repo=pool_repo,
        placement_repo=placement_repo,
        inventory_repo=inventory_repo,
    )

    with patch(
        "orchestration.models.model_deployment.worker.get_adapter",
        return_value=mock_adapter,
    ):
        result = await worker.handle_deploy_requested(deploy_id)

    assert result is None
    mock_adapter.provision_node.assert_not_awaited()
    failed_calls = [
        call
        for call in deploy_repo.update_state.await_args_list
        if len(call.args) >= 2 and call.args[1] == "FAILED"
    ]
    assert failed_calls
    last_call = failed_calls[-1]
    error_message = last_call.kwargs.get(
        "error_message",
        last_call.args[2] if len(last_call.args) > 2 else None,
    )
    assert "on_prem" in error_message
