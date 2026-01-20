from typing import Optional, Dict, Any
from uuid import UUID
import json


class ModelRegistryRepository:
    def __init__(self, db):
        self.db = db

    async def register_model(
        self,
        name: str,
        version: str,
        backend: str,
        artifact_uri: str,
        config: Optional[Dict[str, Any]] = None
    ) -> UUID:
        q = """
        INSERT INTO model_registry (name, version, backend, artifact_uri, config)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING model_id
        """
        async with self.db.acquire() as c:
            return await c.fetchval(
                q, name, version, backend, artifact_uri, json.dumps(config) if config else None
            )

    async def get_model(
        self, name: str, version: str
    ) -> Optional[Dict[str, Any]]:
        q = """
        SELECT model_id, name, version, backend, artifact_uri, config
        FROM model_registry
        WHERE name=$1 AND version=$2
        """
        async with self.db.acquire() as c:
            row = await c.fetchrow(q, name, version)
            return dict(row) if row else None

    async def get_model_by_id(
        self, model_id: UUID
    ) -> Optional[Dict[str, Any]]:
        q = """
        SELECT model_id, name, version, backend, artifact_uri, config
        FROM model_registry
        WHERE model_id=$1
        """
        async with self.db.acquire() as c:
            row = await c.fetchrow(q, model_id)
            return dict(row) if row else None

    async def list_models(self, name: Optional[str] = None):
        if name:
            q = """
            SELECT model_id, name, version, backend, artifact_uri, config
            FROM model_registry
            WHERE name=$1
            ORDER BY created_at DESC
            """
            args = (name,)
        else:
            q = """
            SELECT model_id, name, version, backend, artifact_uri, config
            FROM model_registry
            ORDER BY created_at DESC
            """
            args = ()

        async with self.db.acquire() as c:
            rows = await c.fetch(q, *args)
            return [dict(r) for r in rows]

    async def delete_model(self, model_id: UUID):
        q = "DELETE FROM model_registry WHERE model_id=$1"
        async with self.db.acquire() as c:
            await c.execute(q, model_id)
