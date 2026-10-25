"""Extensions installed alongside InferiaLLM, found through the ``inferia.extensions`` entry point.

Each entry point names a callable that returns an extension object. The gateway
calls its ``mount(app)`` at startup and the migrate step its ``migrate(dsn)``; an
extension may provide either. With nothing installed nothing changes, and an
extension that fails is logged and skipped, never raised: InferiaLLM must start
without it.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import entry_points

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
