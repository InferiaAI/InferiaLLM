"""Dashboard-facing admin router for baking + listing engine-cache AMIs.

Protected by the existing user-JWT + RBAC system (same _need_perm pattern as
admin_workers.py). The heavy bake runs as a background task; status is
best-effort in-memory (lost on CP restart — the AMI artifact is durable and
listed authoritatively via describe_images)."""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/aws/engine-ami")


class _Deps:
    require_permission: Optional[Callable[[str], Any]] = None
    start_bake: Optional[Callable[..., Any]] = None
    list_engine_amis: Optional[Callable[[str], Any]] = None


_deps = _Deps()
# Best-effort in-memory bake status, keyed by bake_id (lost on CP restart).
_BAKES: dict[str, dict] = {}


def configure(
    *,
    require_permission: Callable[[str], Any],
    start_bake: Callable[..., Any] | None = None,
    list_engine_amis: Callable[[str], Any] | None = None,
) -> None:
    _deps.require_permission = require_permission
    _deps.start_bake = start_bake or _default_start_bake
    _deps.list_engine_amis = list_engine_amis or _default_list_engine_amis


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


class BakeRequest(BaseModel):
    region: str
    vllm_tag: Optional[str] = None
    instance_type: Optional[str] = None
    root_volume_gb: Optional[int] = Field(default=None, ge=10, le=16384)
    include_worker_image: bool = True


class BakeResponse(BaseModel):
    bake_id: str
    status: str


@router.post("/bake", response_model=BakeResponse)
async def start_bake(body: BakeRequest, _granted: bool = Depends(_need_perm("deployment:create"))):
    if _deps.start_bake is None:
        raise HTTPException(503, "engine-ami bake not configured")
    bake_id = _deps.start_bake(
        region=body.region,
        vllm_tag=body.vllm_tag,
        instance_type=body.instance_type,
        root_volume_gb=body.root_volume_gb,
        include_worker_image=body.include_worker_image,
    )
    if inspect.iscoroutine(bake_id):
        bake_id = await bake_id
    return BakeResponse(bake_id=bake_id, status="running")


@router.get("/bake/{bake_id}")
async def bake_status(bake_id: str, _granted: bool = Depends(_need_perm("deployment:create"))):
    st = _BAKES.get(bake_id)
    if st is None:
        raise HTTPException(404, "unknown bake id")
    return st


@router.get("")
async def list_amis(region: str = "us-east-1", _granted: bool = Depends(_need_perm("deployment:create"))):
    if _deps.list_engine_amis is None:
        raise HTTPException(503, "engine-ami listing not configured")
    res = _deps.list_engine_amis(region)
    if inspect.iscoroutine(res):
        res = await res
    return {"amis": res}


# --- production defaults (used when configure() gets no overrides) -----------

def _default_start_bake(
    *,
    region: str,
    vllm_tag: Optional[str] = None,
    instance_type: Optional[str] = None,
    root_volume_gb: Optional[int] = None,
    include_worker_image: bool = True,
) -> str:
    """Resolve creds on the loop, run the sync bake in a thread, recording
    best-effort status in _BAKES."""
    from inferia.services.orchestration.services.adapter_engine.adapters.aws.engine_ami_bake import (
        bake_engine_ami,
    )
    from inferia.services.orchestration.services.adapter_engine.aws_orphan_sweep import (
        resolve_sweep_aws_env,
    )

    bake_id = str(uuid.uuid4())
    _BAKES[bake_id] = {"status": "running", "message": "", "ami_id": None, "region": region}
    worker_ref = _worker_image_ref() if include_worker_image else None

    async def _run():
        try:
            aws_env = await resolve_sweep_aws_env()
            kw = {k: v for k, v in {
                "vllm_tag": vllm_tag,
                "instance_type": instance_type,
                "root_volume_gb": root_volume_gb,
            }.items() if v is not None}
            res = await asyncio.to_thread(
                bake_engine_ami,
                region=region,
                aws_env=aws_env,
                worker_image_ref=worker_ref,
                ssm_instance_profile=_ssm_instance_profile(),
                **kw,
            )
            _BAKES[bake_id] = {"status": "succeeded", "message": "", "ami_id": res.ami_id, "region": res.region}
        except Exception as e:  # noqa: BLE001 — surface as best-effort status
            logger.warning("engine-ami bake %s failed: %s", bake_id, e)
            _BAKES[bake_id] = {"status": "failed", "message": str(e), "ami_id": None, "region": region}

    asyncio.create_task(_run())
    return bake_id


async def _default_list_engine_amis(region: str) -> list:
    """Authoritative list via describe_images (creds resolved on the loop, the
    blocking boto3 call offloaded to a thread)."""
    from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
        _ENGINE_CACHE_TAG, _engine_ec2_client,
    )
    from inferia.services.orchestration.services.adapter_engine.aws_orphan_sweep import (
        _creds_from_aws_env, resolve_sweep_aws_env,
    )

    aws_env = await resolve_sweep_aws_env()
    if not aws_env:
        return []
    creds = _creds_from_aws_env(aws_env)
    client = _engine_ec2_client(
        region,
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
    )
    resp = await asyncio.to_thread(
        client.describe_images,
        Owners=["self"],
        Filters=[{"Name": f"tag:{_ENGINE_CACHE_TAG}", "Values": ["true"]}],
    )
    out = []
    for im in resp.get("Images", []) or []:
        tags = {t["Key"]: t["Value"] for t in im.get("Tags", [])}
        out.append({
            "ami_id": im.get("ImageId"),
            "vllm_tag": tags.get("inferia:vllm-tag"),
            "region": region,
            "created": im.get("CreationDate"),
        })
    return out


def _worker_image_ref() -> Optional[str]:
    img = os.environ.get("INFERIA_WORKER_IMAGE", "ghcr.io/inferiaai/inferia-worker")
    tag = os.environ.get("INFERIA_WORKER_IMAGE_TAG", "")
    return f"{img}:{tag}" if tag else None


def _ssm_instance_profile() -> Optional[str]:
    return os.environ.get("INFERIA_BAKE_SSM_INSTANCE_PROFILE") or None


__all__ = ["router", "configure"]
