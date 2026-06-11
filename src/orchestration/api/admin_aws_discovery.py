"""Dashboard-facing admin router for live AWS account discovery (regions +
instance types). Same _need_perm/configure pattern as admin_engine_ami.py;
RBAC is enforced at the gateway proxy. Each route degrades to fallback=True
(empty list) when AWS can't be queried so the pool form keeps working."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from providers.aws.aws_discovery import (
    AwsDiscoveryUnavailable, list_instance_types, list_regions,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin/aws")


class _Deps:
    require_permission: Optional[Callable[[str], Any]] = None


_deps = _Deps()


def configure(*, require_permission: Callable[[str], Any]) -> None:
    _deps.require_permission = require_permission


def _need_perm(perm: str):
    async def _dep(authorization: str | None = Header(default=None)):
        if _deps.require_permission is None:
            raise HTTPException(503, "RBAC dependency not configured")
        check = _deps.require_permission(perm)
        try:
            result = check(authorization)
        except TypeError:
            result = check()
        if hasattr(result, "__await__"):
            result = await result
        return result
    return _dep


@router.get("/regions")
async def get_regions(_granted: bool = Depends(_need_perm("deployment:list"))):
    try:
        return {"regions": await list_regions(), "fallback": False}
    except AwsDiscoveryUnavailable as e:
        logger.info("regions discovery unavailable, returning fallback: %s", e)
        return {"regions": [], "fallback": True}


@router.get("/instance-types")
async def get_instance_types(
    region: str = Query(..., min_length=1),
    _granted: bool = Depends(_need_perm("deployment:list")),
):
    try:
        infos = await list_instance_types(region)
        return {"instance_types": [i.to_dict() for i in infos], "fallback": False}
    except AwsDiscoveryUnavailable as e:
        logger.info("instance-type discovery unavailable, returning fallback: %s", e)
        return {"instance_types": [], "fallback": True}


__all__ = ["router", "configure"]
