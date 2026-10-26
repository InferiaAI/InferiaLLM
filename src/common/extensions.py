"""Extensions installed alongside InferiaLLM, found through the ``inferia.extensions`` entry point.

Each entry point names a callable that returns an extension object. The gateway
calls its ``mount(app)`` at startup, the migrate step its ``migrate(dsn)``, and the
inference app its ``before_request(...)`` on each chat request; an extension may
provide any of them. With nothing installed nothing changes, and an extension
that fails is logged and skipped, never raised: InferiaLLM must work without it.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import entry_points

from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)

GROUP = "inferia.extensions"


@lru_cache(maxsize=1)
def installed() -> tuple:
    found = []
    for ep in entry_points(group=GROUP):
        try:
            found.append(ep.load()())
        except Exception:
            log.exception("extension %s failed to load; skipped", ep.name)
    return tuple(found)


def mount_all(app) -> None:
    for ext in installed():
        if not hasattr(ext, "mount"):
            continue
        try:
            ext.mount(app)
        except Exception:
            log.exception("extension %r failed to mount; skipped", ext)


async def migrate_all(dsn: str) -> None:
    for ext in installed():
        if not hasattr(ext, "migrate"):
            continue
        try:
            await ext.migrate(dsn)
        except Exception:
            log.exception("extension %r failed to migrate; skipped", ext)


async def before_request(**request) -> None:
    """Let extensions check a request before the engine is called.

    ``request`` holds request_id, user_id, org_id, deployment_id, model,
    request_type and body (the JSON body, never file uploads). An extension
    refuses by raising HTTPException, which reaches the client like core's own
    refusals; any other error is logged and the request goes on.
    """
    for ext in installed():
        hook = getattr(ext, "before_request", None)
        if hook is None:
            continue
        try:
            await hook(**request)
        except HTTPException:
            raise
        except Exception:
            log.exception("extension %r failed in before_request; skipped", ext)
