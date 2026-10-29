"""Shared fixtures for the integration tests.

These fixtures stand up a minimal FastAPI app + a real Postgres-backed
ProvisioningReconciler so each test can drive the state machine with
``await app.state.reconciler.tick_once()`` while exercising the full
HTTP surface (GET /provisioning, POST
/provisioning/retry, DELETE /nodes/{id}).

Why a custom factory instead of orchestration.server.serve()?

* serve() is a long-lived async entrypoint that creates a uvicorn
  server, gRPC server, Redis bus, and a *background* reconciler task.
  None of that fits a tick-driven integration test.
* serve() wires the legacy ``NodeProvisioningRepo`` (event-log only)
  as ``provisioning_repo`` on the nodes router. The new state-machine
  surface (retry, cancel) needs the new
  ``ProvisioningJobRepository`` — so the test factory wires that one.

Gated on ``INFERIA_TEST_DATABASE_URL`` — when unset, every test below
is skipped cleanly so CI on machines without a test PG passes.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Migration files applied in order, mirroring cli_init.py's production
# ordering. The 20260528 set lands the node_state='failed' enum value
# (20260528a) before provisioning_jobs references it (20260528b) — the
# split is intentional because Postgres forbids referencing a new enum
# value in the same transaction it was added.
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[4]
    / "infra" / "schema" / "migrations"
)
MIGRATIONS = [
    _MIGRATIONS_DIR / "20260528a_node_state_failed.sql",
    _MIGRATIONS_DIR / "20260528b_provisioning_jobs.sql",
]


@pytest.fixture(autouse=True)
def _pulumi_on_path():
    """Preflight requires the pulumi binary; CI has none and the tests patch
    every pulumi call, so report it present."""
    real_which = shutil.which
    with patch(
        "orchestration.state_machine.phases.preflight.shutil.which",
        side_effect=lambda name, *a, **kw: (
            "/usr/bin/pulumi" if name == "pulumi" else real_which(name, *a, **kw)
        ),
    ):
        yield


@pytest.fixture
def test_database_url() -> str:
    """Test PG dsn. Skips the test if unset so non-DB CI still passes."""
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    """Apply the provisioning migrations in order, each file as one execute
    like cli_init.py does. Idempotent against an already-migrated DB."""
    for path in MIGRATIONS:
        await conn.execute(path.read_text())


ORG_ID = "org-int"
ORG_HEADERS = {"X-Organization-ID": ORG_ID, "Authorization": "Bearer test"}


async def enqueue_aws_node(db_pool, spec: dict) -> str:
    """Create an AWS node the way /deploy's ColdStart does: an org-owned pool,
    a placeholder inventory row and a preflight job. Returns the node id."""
    from uuid import uuid4

    from orchestration.repositories.inventory_repo import InventoryRepository
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )

    pool_id = uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organizations(id, name) VALUES($1, $1) ON CONFLICT DO NOTHING",
            ORG_ID,
        )
        await conn.execute(
            """INSERT INTO compute_pools(
                   id, pool_name, owner_type, owner_id, org_id, provider,
                   pool_type, allowed_gpu_types, max_cost_per_hour,
                   scheduling_policy, provider_pool_id, is_active,
                   lifecycle_state, gpu_count)
               VALUES ($1, $2, 'organization', $3, $3, 'aws', 'cluster',
                       ARRAY[$4], 0, '{}', $5, true, 'running', 1)""",
            pool_id, f"p-{pool_id}", ORG_ID, spec["instance_type"],
            f"placeholder:{pool_id}",
        )
    node_id = await InventoryRepository(db_pool).create_placeholder(
        pool_id=pool_id, gpu_total=1, initial_alloc=0,
    )
    await ProvisioningJobRepository(db_pool).enqueue(
        node_id=node_id, pool_id=pool_id, org_id=ORG_ID, provider="aws",
        spec=spec,
    )
    return str(node_id)


async def _fake_aws_context(job):
    from providers.pulumi.credentials import AWSCredentials

    return AWSCredentials(
        access_key_id="AKIATEST", secret_access_key="test", region="us-east-1",
    ), {}


def _build_app(db_pool, *, reconciler) -> FastAPI:
    """Construct a minimal FastAPI app exposing /v1/nodes/* wired to
    real repos.

    No ``InternalAuthMiddleware`` — the test client doesn't need an
    X-Internal-API-Key, and require_permission is short-circuited to
    always allow so the test doesn't have to mint a JWT.
    """
    from orchestration.api import nodes as nodes_api
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )

    inventory_repo = InventoryRepository(db_pool)
    pool_repo = ComputePoolRepository(db_pool)
    # Wire BOTH repos: ProvisioningJobRepository (new state-machine queue,
    # used by enqueue_aws_node + retry + cancel + the job-row
    # read in GET /provisioning) and NodeProvisioningRepo (legacy event
    # log, used for the phase-summary view and GET /provisioning-logs).
    # Production server.py wires both side-by-side; the integration test
    # mirrors that wiring so we exercise the same code paths.
    from orchestration.repositories.node_provisioning_repo import (
        NodeProvisioningRepo,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )
    provisioning_repo = ProvisioningJobRepository(db_pool)
    node_events_repo = NodeProvisioningRepo(db_pool)

    def _permit_all(_perm):
        async def _check(_authorization=None):
            return True
        return _check

    nodes_api.configure(
        inventory_repo=inventory_repo,
        pool_repo=pool_repo,
        worker_auth=None,
        control_plane_external_url="https://control.example.com",
        adapters={},
        require_permission=_permit_all,
        provisioning_repo=provisioning_repo,
        node_events_repo=node_events_repo,
        db_pool=db_pool,
    )

    app = FastAPI()
    app.include_router(nodes_api.router)
    app.state.pool = db_pool
    app.state.reconciler = reconciler
    return app


@pytest_asyncio.fixture
async def app_with_real_db(test_database_url) -> AsyncIterator[tuple]:
    """Boot the orchestration FastAPI surface against a real test DB
    with a tick-driven ProvisioningReconciler attached to
    ``app.state.reconciler``.

    Yields ``(app, client, pool)``:
      - app:  the FastAPI app (so tests can reach app.state.reconciler)
      - client: an httpx.AsyncClient bound to that app via ASGITransport
      - pool: the asyncpg pool (so tests can read/write directly)

    The reconciler's background loop is NOT started — tests drive it
    via ``await app.state.reconciler.tick_once()`` for deterministic
    state-machine progression.
    """
    from orchestration.state_machine.events import (
        emit_event as _emit_event_to_db,
    )
    from orchestration.state_machine.jobs.model import (
        Phase,
    )
    from orchestration.state_machine.jobs.repository import (
        ProvisioningJobRepository,
    )
    from orchestration.state_machine.phases.bootstrap import (
        BootstrapHandler,
    )
    from orchestration.state_machine.phases.cancel import (
        CancelHandler,
    )
    from orchestration.state_machine.phases.preflight import (
        PreflightHandler,
    )
    from orchestration.state_machine.phases.pulumi_up import (
        PulumiUpHandler,
    )
    from orchestration.state_machine.reconciler.loop import (
        ProvisioningReconciler,
    )
    from orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )

    pool = await asyncpg.create_pool(test_database_url, min_size=2, max_size=10)
    try:
        async with pool.acquire() as conn:
            await _apply_migrations(conn)

        inventory_repo = InventoryRepository(pool)
        repo = ProvisioningJobRepository(pool)

        async def _emit_event(**kwargs):
            await _emit_event_to_db(pool, **kwargs)

        handlers = {
            Phase.PREFLIGHT: PreflightHandler(),
            Phase.PROVISIONING: PulumiUpHandler(),
            Phase.BOOTSTRAPPING: BootstrapHandler(
                inventory_repo=inventory_repo, poll_interval_s=0.01,
            ),
            Phase.CANCELLING: CancelHandler(),
        }

        # Short lease + fast renew so per-tick work doesn't get blocked
        # on a long-running renewer when the test patches sync work
        # synchronously.
        reconciler = ProvisioningReconciler(
            repo=repo,
            handlers=handlers,
            emit_event=_emit_event,
            db=pool,
            concurrency=1,
            poll_interval_s=0.01,
            lease_seconds=30,
            renew_interval_s=5.0,
            lease_holder="test-reconciler",
            # Production loads these from Settings -> Providers -> AWS; the
            # tests patch every AWS call, so any credentials will do.
            load_aws_context=_fake_aws_context,
            # Mirror terminal phases (ready / terminated / failed) onto
            # compute_inventory.state so the integration tests can assert
            # the dashboard-visible state matches the job row's phase.
            inventory_repo=inventory_repo,
        )

        app = _build_app(pool, reconciler=reconciler)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            yield app, client, pool
    finally:
        await pool.close()
