"""Lease renewal coroutine the reconciler runs alongside each handler.

A long-running handler (provisioning, bootstrapping) would otherwise
let its lease expire while still working. The renewal loop UPDATEs
lease_expires_at every renew_interval_s. If a renewal returns False
(lease stolen by another reconciler — shouldn't happen but defensive),
we set the stop event so the surrounding TaskGroup cancels the handler.
"""
from __future__ import annotations

import asyncio
from uuid import UUID


async def renew_loop(
    *,
    repo,
    job_id: UUID,
    lease_holder: str,
    renew_interval_s: float,
    lease_seconds: int,
    stop: asyncio.Event,
) -> bool:
    """Renew the lease until `stop` is set or a renewal fails.

    Returns True if the loop exited cleanly (stop was set), False if a
    renewal returned False (lease stolen).
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=renew_interval_s)
            return True  # stop fired during the sleep
        except asyncio.TimeoutError:
            pass  # interval elapsed; renew now

        ok = await repo.renew_lease(
            job_id=job_id, lease_holder=lease_holder, lease_seconds=lease_seconds,
        )
        if not ok:
            stop.set()
            return False
    return True
