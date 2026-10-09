# api.py
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import deps

router = APIRouter(prefix="/v1/models", tags=["model-cache"])

# Each value needs a download path in downloader.py and a directory layout in
# eviction._dir_for. A value with neither cannot be stored or removed.
CacheSource = Literal["hf", "ollama"]

class AddModelBody(BaseModel):
    source: CacheSource = "hf"
    model_id: str
    revision: str = "main"
    engine: str | None = None

class ImportModelBody(BaseModel):
    # The format decides how the staged directory is read, and the row is
    # stored under that same source — there is no separate 'local' source.
    format: CacheSource = "hf"
    # A name under the staging directory, never a path on the host. Aliased
    # because `from` is a keyword, and the alias is what the schema advertises.
    from_: str = Field(alias="from")
    model_id: str
    revision: str = "main"
    engine: str | None = None

    model_config = {"populate_by_name": True}

@router.get("")
async def list_models():
    return {"models": await deps.get("repo").list_all()}

@router.post("", status_code=202)
async def add_model(body: AddModelBody):
    deps.get("downloader").start(source=body.source, model_id=body.model_id,
                                 revision=body.revision, engine_hint=body.engine)
    return {"status": "downloading", "model_id": body.model_id}

@router.post("/import", status_code=202)
async def import_model(body: ImportModelBody):
    """Bring a model already staged on the control plane into the cache.

    The only way to populate the cache with no internet access. Progress is
    reported through the usual /{cache_id}/progress route.
    """
    paths = deps.get("paths")
    if paths is None:
        raise HTTPException(503, "model cache is not configured")
    try:
        # Rejected here as well as in the download task, so a bad path is a
        # 400 to the caller rather than an error status discovered later.
        paths.resolve_import(body.from_)
    except ValueError as e:
        raise HTTPException(400, str(e))

    dl = deps.get("downloader")
    # start() returns the running task for a key already in flight, so a
    # second import would answer 202 and then be silently dropped.
    if dl.is_running(body.format, body.model_id, body.revision):
        raise HTTPException(409, "a download or import for this model is already running")
    dl.start(
        source=body.format, model_id=body.model_id, revision=body.revision,
        engine_hint=body.engine, import_from=body.from_,
    )
    return {"status": "importing", "model_id": body.model_id}


@router.get("/{cache_id}/progress")
async def progress(cache_id: str):
    row = await deps.get("repo").get(cache_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"status": row["status"], "bytes_total": row["bytes_total"],
            "bytes_done": row["bytes_done"], "error": row.get("error")}

@router.delete("/{cache_id}", status_code=204)
async def delete_model(cache_id: str):
    repo = deps.get("repo")
    row = await repo.get(cache_id)
    if not row:
        raise HTTPException(404, "not found")
    # Stop an in-flight download first, so deleting a model mid-download
    # actually halts the transfer instead of letting it keep writing files.
    dl = deps.get("downloader")
    if dl:
        dl.cancel(
            source=row["source"],
            model_id=row["model_id"],
            revision=row.get("revision", "main"),
        )
    em = deps.get("eviction")
    if em:
        import shutil
        d = em._dir_for(row)
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)
    await repo.delete(cache_id)
    return None
