# api.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from . import deps

router = APIRouter(prefix="/v1/models", tags=["model-cache"])

class AddModelBody(BaseModel):
    source: str = "hf"        # 'hf' | 'ollama'
    model_id: str
    revision: str = "main"
    engine: str | None = None

@router.get("")
async def list_models():
    return {"models": await deps.get("repo").list_all()}

@router.post("", status_code=202)
async def add_model(body: AddModelBody):
    deps.get("downloader").start(source=body.source, model_id=body.model_id,
                                 revision=body.revision, engine_hint=body.engine)
    return {"status": "downloading", "model_id": body.model_id}

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
    em = deps.get("eviction")
    if em:
        import shutil
        shutil.rmtree(em._dir_for(row), ignore_errors=True)
    await repo.delete(cache_id)
    return None
