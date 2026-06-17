"""Unit tests for ``provision_direct_node`` — the shared background helper that
provisions a DePIN/direct-adapter node (Nosana/Akash/k8s) and drives a
deployment to RUNNING, reusing the ``ProviderAdapter`` interface.

Mirrors the proven legacy ``worker.py`` DePIN tail, with two adaptations:
  * it FINALIZES a pre-created placeholder node (by ``node_id``) via
    ``inventory.finalize_direct_node`` rather than ``register_node`` (which
    would create a duplicate row);
  * it runs as a standalone fire-and-forget coroutine, so it owns its own
    try/except and never lets an exception escape.

All adapter/repo mocks are ``AsyncMock(spec=RealClass)`` so a signature drift
in the production call surfaces as a test failure (a bare ``AsyncMock`` would
swallow any kwarg and hide the bug).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orchestration.models.model_deployment import direct_provision
from orchestration.repositories.inventory_repo import InventoryRepository
from orchestration.repositories.model_deployment_repo import (
    ModelDeploymentRepository,
)
from providers.nosana.nosana_adapter import NosanaAdapter

# Capture the real probe before the autouse fixture patches it, so the helper
# tests below exercise the actual implementation (not the mock).
_REAL_WAIT_ENDPOINT_SERVING = direct_provision._wait_endpoint_serving


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------
def _node_spec(**over) -> dict:
    base = {
        "provider": "nosana",
        "provider_instance_id": "job-abc123",
        "hostname": "node-1.nosana.io",
        "gpu_total": 1,
        "vcpu_total": 8,
        "ram_gb_total": 32,
        "region": "global",
        "node_class": "gpu",
        "metadata": {"market": "nosana-rtx3060"},
        "expose_url": "https://expose.nosana.io/abc",
    }
    base.update(over)
    return base


def _deploy(state="PENDING_NODE", **over) -> dict:
    base = {
        "state": state,
        "configuration": {"engine": "ollama", "model_id": "gemma3:4b"},
        "inference_model": "hf://gemma3:4b",
        "model_name": "gemma3",
        "engine": "ollama",
        "pool_id": uuid4(),
        "gpu_per_replica": 1,
    }
    base.update(over)
    return base


def _pool_row(**over) -> dict:
    base = {
        "allowed_gpu_types": ["nosana-rtx3060"],
        "provider_pool_id": "pool-xyz",
        "provider_credential_name": "nosana-main",
    }
    base.update(over)
    return base


def _make_adapter(node_spec=None, ready_url="https://ready.nosana.io/abc"):
    """An ``AsyncMock(spec=NosanaAdapter)`` whose async surface is stubbed."""
    adapter = AsyncMock(spec=NosanaAdapter)
    # get_capabilities is a *sync* method on the real adapter; AsyncMock would
    # make it awaitable, so replace it with a plain callable returning the
    # real capabilities (carries readiness_timeout_seconds).
    adapter.get_capabilities = lambda: NosanaAdapter.CAPABILITIES
    adapter.provision_node.return_value = node_spec if node_spec is not None else _node_spec()
    adapter.wait_for_ready.return_value = ready_url
    return adapter


def _make_deps(deploy=None, finalize_ok=True, get_side_effect=None):
    inventory = AsyncMock(spec=InventoryRepository)
    inventory.finalize_direct_node.return_value = finalize_ok
    deploys = AsyncMock(spec=ModelDeploymentRepository)
    # Guarded transitions (PENDING_NODE->DEPLOYING, DEPLOYING->RUNNING) succeed
    # by default; cancellation tests override this.
    deploys.update_state_if.return_value = True
    if get_side_effect is not None:
        deploys.get.side_effect = get_side_effect
    else:
        deploys.get.return_value = deploy if deploy is not None else _deploy()
    return SimpleNamespace(
        inventory=inventory,
        deploys=deploys,
        db_pool=AsyncMock(),
        controller=AsyncMock(),
        placer=AsyncMock(),
        jobs_repo=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _mock_endpoint_probe():
    """Patch the (HTTP-making) readiness probe so unit tests never hit the
    network. Defaults to ``"ready"``; tests override ``.return_value`` to
    exercise crashed/timeout/cancelled outcomes. The Nosana adapter now
    advertises ``endpoint_http_probeable=True``, so the happy path routes
    through this probe and marks RUNNING only after it returns."""
    with patch.object(
        direct_provision, "_wait_endpoint_serving", new=AsyncMock(return_value="ready")
    ) as m:
        yield m


async def _run(
    adapter,
    deps,
    *,
    node_id=None,
    deploy_id=None,
    gpu=1,
    provider="nosana",
    pool_row=None,
):
    node_id = node_id or uuid4()
    deploy_id = deploy_id or uuid4()
    with patch.object(direct_provision, "get_adapter", return_value=adapter):
        await direct_provision.provision_direct_node(
            deploy_id=deploy_id,
            node_id=node_id,
            pool_row=pool_row if pool_row is not None else _pool_row(),
            pool_meta={},
            provider=provider,
            gpu_per_replica=gpu,
            deps=deps,
        )
    return node_id, deploy_id


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_happy_path_finalizes_and_sets_running():
    spec = _node_spec()
    adapter = _make_adapter(node_spec=spec, ready_url="https://endpoint.nosana.io/run")
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps)

    # provision_node called with the pool's first allowed gpu type + creds
    adapter.provision_node.assert_awaited_once()
    pn = adapter.provision_node.await_args.kwargs
    assert pn["provider_resource_id"] == "nosana-rtx3060"
    assert pn["pool_id"] == "pool-xyz"
    assert pn["provider_credential_name"] == "nosana-main"

    # finalize_direct_node gets the node_spec values for the placeholder
    deps.inventory.finalize_direct_node.assert_awaited_once()
    fk = deps.inventory.finalize_direct_node.await_args.kwargs
    assert fk["node_id"] == node_id
    assert fk["provider_instance_id"] == "job-abc123"
    assert fk["hostname"] == "node-1.nosana.io"
    assert fk["gpu_total"] == 1
    assert fk["vcpu_total"] == 8
    assert fk["ram_gb_total"] == 32
    assert fk["node_class"] == "gpu"
    assert fk["metadata"] == {"market": "nosana-rtx3060"}
    assert fk["expose_url"] == "https://endpoint.nosana.io/run"

    # endpoint updated with the ready url, then RUNNING set
    deps.deploys.update_endpoint.assert_awaited_once()
    ue = deps.deploys.update_endpoint.await_args
    # positional deploy_id + endpoint
    assert ue.args[0] == deploy_id
    assert ue.args[1] == "https://endpoint.nosana.io/run"

    # For a probeable provider (Nosana), RUNNING is gated on the endpoint probe:
    # PENDING_NODE -> DEPLOYING, probe ("ready"), then DEPLOYING -> RUNNING — both
    # via the atomic, event-publishing update_state_if (NOT set_state).
    transitions = [
        (c.args[0], c.args[1], c.args[2]) for c in deps.deploys.update_state_if.await_args_list
    ]
    assert (deploy_id, "PENDING_NODE", "DEPLOYING") in transitions
    assert (deploy_id, "DEPLOYING", "RUNNING") in transitions
    # the readiness probe was actually consulted
    direct_provision._wait_endpoint_serving.assert_awaited_once()
    # Regression guard: set_state must NOT have been used for any transition.
    for c in deps.deploys.set_state.await_args_list:
        assert c.args[1] != "RUNNING", "set_state used for RUNNING — use update_state_if"
    # deprovision lives on the ADAPTER, not the inventory repo; never called
    # on the happy path.
    adapter.deprovision_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_happy_path_builds_metadata_from_configuration():
    spec = _node_spec()
    adapter = _make_adapter(node_spec=spec)
    deps = _make_deps(deploy=_deploy())

    await _run(adapter, deps)

    md = adapter.provision_node.await_args.kwargs["metadata"]
    # model identifiers injected from the deploy row
    assert md["model_id"] == "hf://gemma3:4b"
    assert md["model_name"] == "gemma3"
    assert md["engine"] == "ollama"


# ---------------------------------------------------------------------------
# 2. provision_node raises -> FAILED + release_gpu, RUNNING never set
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provision_node_raises_sets_failed_and_releases():
    adapter = _make_adapter()
    adapter.provision_node.side_effect = RuntimeError("nosana market down")
    deps = _make_deps(deploy=_deploy())

    # must NOT propagate (background task)
    node_id, deploy_id = await _run(adapter, deps, gpu=2)

    # FAILED recorded with the error message (use update_state which carries it)
    deps.deploys.update_state.assert_awaited()
    failed_call = deps.deploys.update_state.await_args
    assert failed_call.args[0] == deploy_id
    assert failed_call.args[1] == "FAILED"
    assert "nosana market down" in (
        failed_call.kwargs.get("error_message", "")
        or (failed_call.args[2] if len(failed_call.args) > 2 else "")
    )

    deps.inventory.release_gpu.assert_awaited_once_with(node_id, 2)
    # mark_terminated must also be called so the reaper/refcount logic frees
    # the placeholder node.
    deps.inventory.mark_terminated.assert_awaited_once_with(node_id)
    # RUNNING never set via set_state or update_state
    for c in deps.deploys.set_state.await_args_list:
        assert c.args[1] != "RUNNING"
    # update_state should only have been called with FAILED (not RUNNING)
    for c in deps.deploys.update_state.await_args_list:
        assert c.args[1] != "RUNNING"


# ---------------------------------------------------------------------------
# 3. Cancellation between provision and finalize -> deprovision, no RUNNING
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancelled_during_provisioning_deprovisions():
    adapter = _make_adapter()
    # first get() -> PENDING_NODE; guard get() after wait_for_ready -> CANCELLED
    deps = _make_deps(
        get_side_effect=[_deploy(state="PENDING_NODE"), _deploy(state="CANCELLED")]
    )

    await _run(adapter, deps)

    adapter.deprovision_node.assert_awaited_once()
    dn = adapter.deprovision_node.await_args.kwargs
    assert dn["provider_instance_id"] == "job-abc123"
    assert dn["provider_credential_name"] == "nosana-main"

    deps.inventory.finalize_direct_node.assert_not_awaited()
    deps.deploys.set_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. finalize returns False (placeholder gone) -> deprovision, no RUNNING
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_false_deprovisions_no_running():
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy(), finalize_ok=False)

    await _run(adapter, deps)

    deps.inventory.finalize_direct_node.assert_awaited_once()
    adapter.deprovision_node.assert_awaited_once()
    # RUNNING never set, endpoint never updated
    deps.deploys.set_state.assert_not_awaited()
    deps.deploys.update_endpoint.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. wait_for_ready returns a sentinel -> expose_url falls back to node_spec
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wait_ready_confidential_sentinel_falls_back_to_expose_url():
    spec = _node_spec(expose_url="https://fallback.nosana.io/x")
    adapter = _make_adapter(node_spec=spec, ready_url="job-running-confidential")
    deps = _make_deps(deploy=_deploy())

    await _run(adapter, deps)

    fk = deps.inventory.finalize_direct_node.await_args.kwargs
    assert fk["expose_url"] == "https://fallback.nosana.io/x"
    ue = deps.deploys.update_endpoint.await_args
    assert ue.args[1] == "https://fallback.nosana.io/x"


@pytest.mark.asyncio
async def test_wait_ready_ready_suffix_sentinel_falls_back_to_expose_url():
    spec = _node_spec(expose_url="https://fallback.nosana.io/y")
    adapter = _make_adapter(node_spec=spec, ready_url="some-node-ready")
    deps = _make_deps(deploy=_deploy())

    await _run(adapter, deps)

    fk = deps.inventory.finalize_direct_node.await_args.kwargs
    assert fk["expose_url"] == "https://fallback.nosana.io/y"


# ---------------------------------------------------------------------------
# 6. simulation mode -> RUNNING set, short-circuit (no wait/finalize)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_simulation_mode_short_circuits_to_running():
    spec = _node_spec(metadata={"mode": "simulation"})
    adapter = _make_adapter(node_spec=spec)
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps)

    # RUNNING must be signalled via update_state (publishes deployment.state_changed),
    # NOT set_state (which is intentionally event-silent).
    deps.deploys.update_state.assert_awaited_once_with(deploy_id, "RUNNING")
    # Regression guard: set_state must NOT have been called with "RUNNING".
    for c in deps.deploys.set_state.await_args_list:
        assert c.args[1] != "RUNNING", "set_state used for RUNNING — use update_state"
    adapter.wait_for_ready.assert_not_awaited()
    deps.inventory.finalize_direct_node.assert_not_awaited()
    adapter.deprovision_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# deploy not found -> log + return, no provisioning
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deploy_missing_returns_early():
    adapter = _make_adapter()
    deps = _make_deps(get_side_effect=[None])

    await _run(adapter, deps)

    adapter.provision_node.assert_not_awaited()
    deps.deploys.set_state.assert_not_awaited()
    deps.deploys.update_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# NEW: 10. Unknown provider -> FAILED marked, no deprovision, no exception
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_provider_marks_failed():
    """get_adapter raises ValueError for an unknown provider; must mark FAILED
    and return without attempting deprovision (no external instance was created).
    The exception must not escape the coroutine."""
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())
    deploy_id = uuid4()

    with patch.object(
        direct_provision, "get_adapter", side_effect=ValueError("unknown provider: bad-provider")
    ):
        # Must NOT propagate (fire-and-forget)
        await direct_provision.provision_direct_node(
            deploy_id=deploy_id,
            node_id=uuid4(),
            pool_row=_pool_row(),
            pool_meta={},
            provider="bad-provider",
            gpu_per_replica=1,
            deps=deps,
        )

    # FAILED must be recorded
    deps.deploys.update_state.assert_awaited_once()
    call_args = deps.deploys.update_state.await_args
    assert call_args.args[0] == deploy_id
    assert call_args.args[1] == "FAILED"
    assert "unknown provider" in (
        call_args.kwargs.get("error_message", "")
        or (call_args.args[2] if len(call_args.args) > 2 else "")
    )

    # No external instance was created, so deprovision must never be called
    adapter.deprovision_node.assert_not_awaited()
    # provision_node must not have been called either
    adapter.provision_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# NEW: 11. First fetch returns non-PENDING_NODE -> abort before provision
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_first_fetch_not_pending_aborts_before_provision():
    """If the deployment is not in PENDING_NODE state when first fetched
    (e.g. already CANCELLED), we must abort without provisioning a paid instance."""
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy(state="CANCELLED"))

    node_id, deploy_id = await _run(adapter, deps)

    # provision_node must NOT have been called (no paid instance created)
    adapter.provision_node.assert_not_awaited()
    # No RUNNING state should be set
    deps.deploys.update_state.assert_not_awaited()
    deps.deploys.set_state.assert_not_awaited()
    # No deprovision since no instance was created
    adapter.deprovision_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# NEW: 12. Empty allowed_gpu_types -> FAILED with clear message
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_allowed_gpu_types_marks_failed():
    """If the pool has no GPU types configured, mark FAILED with a clear
    error message rather than letting an IndexError escape or produce an
    opaque failure."""
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())
    deploy_id = uuid4()

    with patch.object(direct_provision, "get_adapter", return_value=adapter):
        await direct_provision.provision_direct_node(
            deploy_id=deploy_id,
            node_id=uuid4(),
            pool_row=_pool_row(allowed_gpu_types=[]),  # empty
            pool_meta={},
            provider="nosana",
            gpu_per_replica=1,
            deps=deps,
        )

    # provision_node must NOT have been called
    adapter.provision_node.assert_not_awaited()

    # FAILED must be recorded with the clear message
    deps.deploys.update_state.assert_awaited_once()
    call_args = deps.deploys.update_state.await_args
    assert call_args.args[0] == deploy_id
    assert call_args.args[1] == "FAILED"
    error_msg = call_args.kwargs.get("error_message", "") or (
        call_args.args[2] if len(call_args.args) > 2 else ""
    )
    assert "allowed_gpu_types" in error_msg or "GPU type" in error_msg


@pytest.mark.asyncio
async def test_absent_allowed_gpu_types_marks_failed():
    """If allowed_gpu_types key is absent from pool_row, treat same as empty."""
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())
    deploy_id = uuid4()

    pool = _pool_row()
    del pool["allowed_gpu_types"]  # key absent

    with patch.object(direct_provision, "get_adapter", return_value=adapter):
        await direct_provision.provision_direct_node(
            deploy_id=deploy_id,
            node_id=uuid4(),
            pool_row=pool,
            pool_meta={},
            provider="nosana",
            gpu_per_replica=1,
            deps=deps,
        )

    adapter.provision_node.assert_not_awaited()
    deps.deploys.update_state.assert_awaited_once()
    call_args = deps.deploys.update_state.await_args
    assert call_args.args[1] == "FAILED"


# ---------------------------------------------------------------------------
# NEW: 13. wait_for_ready raises -> deprovision + FAILED + release_gpu
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wait_for_ready_raises_deprovisions_and_fails():
    """If wait_for_ready raises, we should: mark FAILED, release_gpu,
    mark_terminated, AND deprovision the external instance using the captured
    provider_instance_id."""
    adapter = _make_adapter()
    adapter.wait_for_ready.side_effect = RuntimeError("node never became ready")
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps, gpu=1)

    # FAILED must be recorded
    deps.deploys.update_state.assert_awaited()
    failed_call = deps.deploys.update_state.await_args
    assert failed_call.args[0] == deploy_id
    assert failed_call.args[1] == "FAILED"
    assert "node never became ready" in (
        failed_call.kwargs.get("error_message", "")
        or (failed_call.args[2] if len(failed_call.args) > 2 else "")
    )

    # GPU release and node termination must happen
    deps.inventory.release_gpu.assert_awaited_once_with(node_id, 1)
    deps.inventory.mark_terminated.assert_awaited_once_with(node_id)

    # The external instance must be deprovisioned using the captured provider_instance_id
    adapter.deprovision_node.assert_awaited_once()
    dn_kwargs = adapter.deprovision_node.await_args.kwargs
    assert dn_kwargs["provider_instance_id"] == "job-abc123"
    assert dn_kwargs["provider_credential_name"] == "nosana-main"

    # RUNNING must never be set
    for c in deps.deploys.update_state.await_args_list:
        assert c.args[1] != "RUNNING"


# ---------------------------------------------------------------------------
# NEW: 14. HF token flows from configuration.env into provision_node metadata
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hf_token_flows_from_configuration_env():
    """The HuggingFace token stored in configuration.env.HF_TOKEN at deploy-
    create time must flow through _build_metadata into the metadata passed to
    provision_node. The function must NOT re-resolve the token from the DB."""
    spec = _node_spec()
    adapter = _make_adapter(node_spec=spec)
    deploy = _deploy(
        configuration={"env": {"HF_TOKEN": "hf_x"}, "engine": "vllm"}
    )
    deps = _make_deps(deploy=deploy)

    await _run(adapter, deps)

    md = adapter.provision_node.await_args.kwargs["metadata"]
    assert md.get("env", {}).get("HF_TOKEN") == "hf_x"


# ---------------------------------------------------------------------------
# NEW: 15. Cancellation and finalize-False paths do NOT release_gpu / mark_terminated
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancellation_path_does_not_release_gpu():
    """On the cancellation-guard path (state changed after wait_for_ready),
    release_gpu and mark_terminated must NOT be called — those are owned by
    the cancel/delete flow that changed the state (atomic-refcount contract).
    The external instance IS deprovisioned."""
    adapter = _make_adapter()
    # First get() -> PENDING_NODE (passes early-abort); guard get() -> CANCELLED
    deps = _make_deps(
        get_side_effect=[_deploy(state="PENDING_NODE"), _deploy(state="CANCELLED")]
    )

    await _run(adapter, deps)

    # GPU release and termination must NOT happen (owned by cancel flow)
    deps.inventory.release_gpu.assert_not_awaited()
    deps.inventory.mark_terminated.assert_not_awaited()

    # External instance IS deprovisioned
    adapter.deprovision_node.assert_awaited_once()
    dn = adapter.deprovision_node.await_args.kwargs
    assert dn["provider_instance_id"] == "job-abc123"


# ---------------------------------------------------------------------------
# Readiness probe outcomes (the premature-RUNNING gate)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_probe_crashed_marks_failed_and_deprovisions(_mock_endpoint_probe):
    """If the endpoint never serves because the provider job crashed during
    model load, mark FAILED + release_gpu + mark_terminated + deprovision."""
    _mock_endpoint_probe.return_value = "crashed"
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps, gpu=1)

    # FAILED with a clear cause (container crashed on the node)
    failed = [c for c in deps.deploys.update_state.await_args_list if c.args[1] == "FAILED"]
    assert failed, "expected a FAILED transition"
    msg = failed[-1].kwargs.get("error_message", "")
    assert "crash" in msg.lower() or "exited" in msg.lower()
    # teardown
    deps.inventory.release_gpu.assert_awaited_once_with(node_id, 1)
    deps.inventory.mark_terminated.assert_awaited_once_with(node_id)
    adapter.deprovision_node.assert_awaited_once()
    # never reached the RUNNING transition
    rs = [c for c in deps.deploys.update_state_if.await_args_list if c.args[2] == "RUNNING"]
    assert not rs, "RUNNING must not be set when the endpoint never served"


@pytest.mark.asyncio
async def test_probe_timeout_still_marks_running(_mock_endpoint_probe):
    """A slow node that never served within the window is NOT failed (it may
    still come up) — mark RUNNING and let the liveness worker reconcile."""
    _mock_endpoint_probe.return_value = "timeout"
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps)

    transitions = [
        (c.args[1], c.args[2]) for c in deps.deploys.update_state_if.await_args_list
    ]
    assert ("DEPLOYING", "RUNNING") in transitions
    # not failed
    for c in deps.deploys.update_state.await_args_list:
        assert c.args[1] != "FAILED"
    adapter.deprovision_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_cancelled_deprovisions_no_running(_mock_endpoint_probe):
    """If the deploy is cancelled during the probe, deprovision the external
    instance and do NOT mark RUNNING — and do NOT release_gpu/mark_terminated
    (owned by the cancel flow)."""
    _mock_endpoint_probe.return_value = "cancelled"
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy())

    await _run(adapter, deps)

    adapter.deprovision_node.assert_awaited_once()
    deps.inventory.release_gpu.assert_not_awaited()
    deps.inventory.mark_terminated.assert_not_awaited()
    rs = [c for c in deps.deploys.update_state_if.await_args_list if c.args[2] == "RUNNING"]
    assert not rs


@pytest.mark.asyncio
async def test_non_probeable_provider_marks_running_unconditionally(_mock_endpoint_probe):
    """A provider that does NOT advertise endpoint_http_probeable keeps the
    original behavior: mark RUNNING via update_state once scheduled, no probe."""
    from dataclasses import replace
    adapter = _make_adapter()
    caps = replace(NosanaAdapter.CAPABILITIES, endpoint_http_probeable=False)
    adapter.get_capabilities = lambda: caps
    deps = _make_deps(deploy=_deploy())

    node_id, deploy_id = await _run(adapter, deps)

    deps.deploys.update_state.assert_awaited_once_with(deploy_id, "RUNNING")
    _mock_endpoint_probe.assert_not_awaited()


# ---------------------------------------------------------------------------
# _wait_endpoint_serving helper (the HTTP probe itself)
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        st = self._statuses.pop(0) if self._statuses else 503
        return _FakeResp(st)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _probe_adapter(status="RUNNING"):
    a = AsyncMock(spec=NosanaAdapter)
    a.get_node_status.return_value = status
    return a


@pytest.mark.asyncio
async def test_probe_helper_ready_on_200():
    deps = _make_deps(deploy=_deploy(state="DEPLOYING"))
    adapter = _probe_adapter("RUNNING")
    sess = _FakeSession([200])
    with patch.object(direct_provision.aiohttp, "ClientSession", return_value=sess):
        res = await _REAL_WAIT_ENDPOINT_SERVING(
            adapter=adapter, expose_url="https://n.nos.ci/", provider_instance_id="j1",
            cred_name="c", deploy_id=uuid4(), deps=deps, timeout=60,
        )
    assert res == "ready"
    assert sess.urls[0].endswith("/health")


@pytest.mark.asyncio
async def test_probe_helper_crashed_on_terminal_job():
    deps = _make_deps(deploy=_deploy(state="DEPLOYING"))
    adapter = _probe_adapter("STOPPED")  # terminal
    sess = _FakeSession([503])
    with patch.object(direct_provision.aiohttp, "ClientSession", return_value=sess):
        res = await _REAL_WAIT_ENDPOINT_SERVING(
            adapter=adapter, expose_url="https://n.nos.ci", provider_instance_id="j1",
            cred_name="c", deploy_id=uuid4(), deps=deps, timeout=60,
        )
    assert res == "crashed"


@pytest.mark.asyncio
async def test_probe_helper_cancelled_when_not_deploying():
    deps = _make_deps(deploy=_deploy(state="TERMINATED"))  # left DEPLOYING
    adapter = _probe_adapter("RUNNING")
    sess = _FakeSession([200])
    with patch.object(direct_provision.aiohttp, "ClientSession", return_value=sess):
        res = await _REAL_WAIT_ENDPOINT_SERVING(
            adapter=adapter, expose_url="https://n.nos.ci", provider_instance_id="j1",
            cred_name="c", deploy_id=uuid4(), deps=deps, timeout=60,
        )
    assert res == "cancelled"


@pytest.mark.asyncio
async def test_probe_helper_timeout(monkeypatch):
    deps = _make_deps(deploy=_deploy(state="DEPLOYING"))
    adapter = _probe_adapter("RUNNING")  # not terminal
    sess = _FakeSession([503] * 50)  # never 200
    # jump the clock past the timeout after the first read; inexhaustible so
    # unrelated time.monotonic() callers don't trip StopIteration; no real sleep
    import itertools
    ticks = itertools.chain([0.0], itertools.repeat(1000.0))
    monkeypatch.setattr(direct_provision.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(direct_provision.asyncio, "sleep", AsyncMock())
    with patch.object(direct_provision.aiohttp, "ClientSession", return_value=sess):
        res = await _REAL_WAIT_ENDPOINT_SERVING(
            adapter=adapter, expose_url="https://n.nos.ci", provider_instance_id="j1",
            cred_name="c", deploy_id=uuid4(), deps=deps, timeout=60,
        )
    assert res == "timeout"


@pytest.mark.asyncio
async def test_finalize_false_path_does_not_release_gpu():
    """On the finalize-returns-False path (placeholder node gone concurrently),
    release_gpu and mark_terminated must NOT be called — those are owned by
    the cancel/delete flow that removed the placeholder (atomic-refcount
    contract). The external instance IS deprovisioned."""
    adapter = _make_adapter()
    deps = _make_deps(deploy=_deploy(), finalize_ok=False)

    await _run(adapter, deps)

    # GPU release and termination must NOT happen (owned by cancel flow)
    deps.inventory.release_gpu.assert_not_awaited()
    deps.inventory.mark_terminated.assert_not_awaited()

    # External instance IS deprovisioned
    adapter.deprovision_node.assert_awaited_once()
    dn = adapter.deprovision_node.await_args.kwargs
    assert dn["provider_instance_id"] == "job-abc123"
