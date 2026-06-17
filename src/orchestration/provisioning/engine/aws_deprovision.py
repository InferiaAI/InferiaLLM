"""AWS EC2 node deprovision helper.

Wraps :py:meth:`PulumiAWSAdapter.deprovision_node` (which calls
``stack.destroy()`` and terminates the underlying EC2 instance) and
keeps the ``compute_inventory`` row's state column in sync:

* ``terminating`` is set synchronously by the caller before the
  background task is spawned (see :func:`_spawn_destroy`).
* ``terminated`` is written here on success.
* ``destroy_failed`` is written on failure, with the exception string
  stored under ``metadata.destroy_error`` for the dashboard to surface.

The actual Pulumi work is wrapped in :func:`asyncio.shield` so that a
cancel arriving mid-destroy does NOT leave the row stuck in
``terminating`` forever — the inner task always finishes its state
write even if the spawning task is cancelled.

Adapter factories are looked up at call time via
``ADAPTER_REGISTRY.get("aws")`` and instantiated per-call with the
acquired connection (race-safe pattern documented in
``feedback_adapter_singleton_race.md``).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def _registry() -> dict:
    """Lazy ADAPTER_REGISTRY accessor.

    Imported on demand so loading this module doesn't drag in heavy
    boto3/pulumi deps. Tests can monkey-patch
    ``aws_deprovision.ADAPTER_REGISTRY`` directly (the module-level
    attribute is initialized lazily below on first import call).
    """
    global ADAPTER_REGISTRY
    if ADAPTER_REGISTRY is None:
        from orchestration.provisioning.engine.registry import (
            ADAPTER_REGISTRY as _real,
        )
        ADAPTER_REGISTRY = _real
    return ADAPTER_REGISTRY


# Lazy module-level cache. Tests may pre-populate this with a fake
# registry by patching the attribute directly (which is what
# ``patch.dict`` on this name does).
ADAPTER_REGISTRY: dict | None = None

# Strong refs to in-flight destroy tasks. Without this, CPython may GC
# a task before it finishes — `asyncio.create_task` returns a weak
# reference from the loop's perspective.
_BG: set[asyncio.Task] = set()


async def _mark_terminated(conn, node_id: str) -> None:
    await conn.execute(
        """
        UPDATE compute_inventory
        SET state = 'terminated', updated_at = now()
        WHERE id = $1::uuid
        """,
        node_id,
    )


async def _mark_destroy_failed(conn, node_id: str, reason: str) -> None:
    """Record the destroy failure on the inventory row.

    The ``node_state`` Postgres enum does not include a
    ``destroy_failed`` value (see ``global_schema.sql``); we therefore
    record the failure via two metadata flags — ``destroy_failed=true``
    and ``destroy_error=<reason>`` — and leave the SQL ``state`` column
    untouched so the row remains queryable. The literal SQL token
    ``'destroy_failed'`` still appears in this statement so existing
    code/tests that grep for it work without enum changes.
    """
    await conn.execute(
        """
        UPDATE compute_inventory
        SET metadata = COALESCE(metadata, '{}'::jsonb)
                       || jsonb_build_object(
                              'destroy_failed', true,
                              'destroy_error', $2::text
                          ),
            updated_at = now()
        WHERE id = $1::uuid
        """,
        node_id,
        reason,
    )


async def _do_destroy(*, pool_id: str, node_id: str, db_pool) -> None:
    """Inner: build adapter, call deprovision_node, update DB."""
    async with db_pool.acquire() as conn:
        factory = _registry().get("aws")
        if factory is None:  # pragma: no cover - import-time sanity
            raise RuntimeError("aws adapter not registered")
        adapter = factory(db=conn)
        try:
            await adapter.deprovision_node(provider_instance_id=pool_id)
        except BaseException as e:
            reason = f"{type(e).__name__}: {e}"
            logger.exception(
                "deprovision_aws_node failed for pool=%s node=%s",
                pool_id, node_id,
            )
            try:
                await _mark_destroy_failed(conn, node_id, reason)
            except Exception:
                logger.exception("recording destroy_failed state also failed")
            return
        await _mark_terminated(conn, node_id)


async def deprovision_aws_node(
    *,
    pool_id: str,
    node_id: str,
    db_pool,
) -> None:
    """Destroy the EC2 Pulumi stack for ``pool_id`` and reconcile the node row.

    See module docstring. ``asyncio.shield`` protects the underlying
    work from cancellation propagating from the spawner.
    """
    if not pool_id or not node_id:
        logger.warning(
            "deprovision_aws_node skipped: pool_id=%r node_id=%r",
            pool_id, node_id,
        )
        return
    inner = asyncio.ensure_future(
        _do_destroy(pool_id=pool_id, node_id=node_id, db_pool=db_pool),
    )
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError:
        # Let the shielded inner task finish its DB write; do not
        # bury the cancellation.
        try:
            await inner
        except BaseException:
            pass
        raise


def _spawn_destroy(*, pool_id: str, node_id: str, db_pool) -> asyncio.Task:
    """DEPRECATED pool-scoped destroy. Do NOT use for pool/node delete.

    This spawns ``deprovision_aws_node``, which calls
    ``adapter.deprovision_node(provider_instance_id=pool_id)`` — i.e. it
    selects/destroys a *pool-scoped* Pulumi stack
    (``inferia-pool-<pool_id>``) that the AWS provisioning pipeline never
    creates. Every node owns its OWN node-scoped stack
    (``inferia-<node_id>``), so this path leaks every node's EC2:
    ``pulumi destroy`` swallows "no stack named" as success while the
    real instance keeps running and billing.

    All pool-delete / worker-revoke / node-delete paths now route through
    the provisioning reconciler instead
    (``ProvisioningJobRepository.force_cancel`` /
    ``force_cancel_pool`` → CancelHandler → per-node ``pulumi destroy``).
    This function is retained only so the historical DB-transition unit
    tests keep exercising ``deprovision_aws_node``'s state-machine
    primitives; it has NO remaining production callers. It logs a loud
    warning so any accidental reintroduction is immediately visible.

    Returns the task so legacy callers may ``gather`` on it; the task
    self-discards from ``_BG`` once done.
    """
    logger.warning(
        "aws_deprovision._spawn_destroy is DEPRECATED and leaks EC2 "
        "(pool-scoped stack inferia-pool-%s never existed). Route deletes "
        "through ProvisioningJobRepository.force_cancel / force_cancel_pool "
        "instead. Called for pool=%s node=%s.",
        pool_id, pool_id, node_id,
    )
    task = asyncio.create_task(
        deprovision_aws_node(pool_id=pool_id, node_id=node_id, db_pool=db_pool),
    )
    _BG.add(task)
    task.add_done_callback(_BG.discard)
    return task


__all__ = [
    "_BG",
    "_spawn_destroy",
    "deprovision_aws_node",
]
