"""Shared fixtures for the integration tests.

These fixtures stand up a minimal FastAPI app + a real Postgres-backed
ProvisioningReconciler so each test can drive the state machine with
``await app.state.reconciler.tick_once()`` while exercising the full
HTTP surface (POST /v1/nodes/add/aws, GET /provisioning, POST
/provisioning/retry, DELETE /nodes/{id}).

Why a custom factory instead of orchestration.server.serve()?

* serve() is a long-lived async entrypoint that creates a uvicorn
  server, gRPC server, Redis bus, and a *background* reconciler task.
  None of that fits a tick-driven integration test.
* serve() wires the legacy ``NodeProvisioningRepo`` (event-log only)
  as ``provisioning_repo`` on the nodes router. The new state-machine
  surface (POST /v1/nodes/add/aws, retry, cancel) needs the new
  ``ProvisioningJobRepository`` — so the test factory wires that one.

Gated on ``INFERIA_TEST_DATABASE_URL`` — when unset, every test below
is skipped cleanly so CI on machines without a test PG passes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

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
    Path(__file__).resolve().parents[6]
    / "infra" / "schema" / "migrations"
)
MIGRATIONS = [
    _MIGRATIONS_DIR / "20260528a_node_state_failed.sql",
    _MIGRATIONS_DIR / "20260528b_provisioning_jobs.sql",
]


@pytest.fixture
def test_database_url() -> str:
    """Test PG dsn. Skips the test if unset so non-DB CI still passes."""
    url = os.environ.get("INFERIA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INFERIA_TEST_DATABASE_URL not set")
    return url


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    """Apply the provisioning migrations in order, splitting each file
    on ';' the same way cli_init.py does in production. Idempotent
    against an already-migrated DB."""
    for path in MIGRATIONS:
        sql = path.read_text()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(stmt)


def _build_app(db_pool, *, reconciler) -> FastAPI:
    """Construct a minimal FastAPI app exposing /v1/nodes/* wired to
    real repos.

    No ``InternalAuthMiddleware`` — the test client doesn't need an
    X-Internal-API-Key, and require_permission is short-circuited to
    always allow so the test doesn't have to mint a JWT.
    """
    from inferia.services.orchestration.api import nodes as nodes_api
    from inferia.services.orchestration.repositories.inventory_repo import (
        InventoryRepository,
    )
    from inferia.services.orchestration.repositories.pool_repo import (
        ComputePoolRepository,
    )

    inventory_repo = InventoryRepository(db_pool)
    pool_repo = ComputePoolRepository(db_pool)
    # Use the new state-machine repo so the thin-enqueue path + retry +
    # cancel routes resolve against the same provisioning_jobs row the
    # reconciler claims. Note: production server.py wires NodeProvisioningRepo
    # (event log) here; that's a known plan-drift item flagged in the
    # task notes — the test fixture deliberately deviates so it exercises
    # the integration contract the new endpoints actually depend on.
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )
    provisioning_repo = ProvisioningJobRepository(db_pool)

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
    from inferia.services.orchestration.services.provisioning.events import (
        emit_event as _emit_event_to_db,
    )
    from inferia.services.orchestration.services.provisioning.jobs.model import (
        Phase,
    )
    from inferia.services.orchestration.services.provisioning.jobs.repository import (
        ProvisioningJobRepository,
    )
    from inferia.services.orchestration.services.provisioning.phases.bootstrap import (
        BootstrapHandler,
    )
    from inferia.services.orchestration.services.provisioning.phases.cancel import (
        CancelHandler,
    )
    from inferia.services.orchestration.services.provisioning.phases.preflight import (
        PreflightHandler,
    )
    from inferia.services.orchestration.services.provisioning.phases.pulumi_up import (
        PulumiUpHandler,
    )
    from inferia.services.orchestration.services.provisioning.reconciler.loop import (
        ProvisioningReconciler,
    )
    from inferia.services.orchestration.repositories.inventory_repo import (
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
        )

        app = _build_app(pool, reconciler=reconciler)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            yield app, client, pool
    finally:
        await pool.close()
