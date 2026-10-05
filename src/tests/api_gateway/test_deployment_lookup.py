"""A deployment must answer to the identifier the product shows for it.

The dashboard's Model column renders `inference_model`, which is the model id.
The lookup matched `model_name` and `llmd_resource_name` only, and the deploy
form writes the deployment's *name* into `model_name` — so the one identifier a
user could see was the one that returned 401.

These tests read the compiled WHERE clause rather than mocking `db.execute`
wholesale. A mock that returns a row proves nothing about which columns were
asked for, which is how this survived.
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import cachetools
import pytest

from api_gateway.policy.engine import PolicyEngine


def _engine():
    engine = PolicyEngine.__new__(PolicyEngine)
    engine._cache_lock = asyncio.Lock()
    engine.context_cache = cachetools.TTLCache(maxsize=10, ttl=30)
    engine.verify_api_key = AsyncMock()
    return engine


# The column is UUID(as_uuid=True), so this is what the row actually holds.
BOUND_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _key(org_id="org-1", deployment_id=None):
    return MagicMock(org_id=org_id, deployment_id=deployment_id)


def _db_returning_nothing():
    """A session that finds no deployment, and keeps the WHERE clause it was given.

    The WHERE clause, not the whole statement: every column appears in the SELECT
    list, so searching the full SQL for a column name passes whether or not that
    column is being matched on.
    """
    db = AsyncMock()
    seen = {}

    async def _execute(stmt, *a, **k):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        seen["where"] = sql.split("WHERE ", 1)[1] if "WHERE " in sql else ""
        result = MagicMock()
        result.first.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)
    return db, seen


@pytest.mark.asyncio
class TestWhichColumnsAreSearched:

    async def test_the_model_id_column_is_searched(self):
        """`inference_model` is what the dashboard displays, so it has to match."""
        engine = _engine()
        engine.verify_api_key.return_value = _key()
        db, seen = _db_returning_nothing()

        await engine.resolve_context(db, "sk-1", "Qwen/Qwen2.5-1.5B-Instruct")

        assert "inference_model" in seen["where"]

    async def test_the_other_two_names_still_match(self):
        """Adding one identifier must not drop the two that already worked."""
        engine = _engine()
        engine.verify_api_key.return_value = _key()
        db, seen = _db_returning_nothing()

        await engine.resolve_context(db, "sk-1", "anything")

        assert "model_name" in seen["where"]
        assert "llmd_resource_name" in seen["where"]

    async def test_a_bound_key_does_not_search_by_model_at_all(self):
        """A key tied to one deployment reaches it whatever model is asked for."""
        engine = _engine()
        engine.verify_api_key.return_value = _key(deployment_id=BOUND_ID)
        db, seen = _db_returning_nothing()

        await engine.resolve_context(db, "sk-1", "anything")

        assert "inference_model" not in seen["where"]
        assert "llmd_resource_name" not in seen["where"]

    async def test_the_org_still_bounds_the_search(self):
        """Matching one more column must not widen it past the caller's org."""
        engine = _engine()
        engine.verify_api_key.return_value = _key(org_id="org-1")
        db, seen = _db_returning_nothing()

        await engine.resolve_context(db, "sk-1", "anything")

        assert "org_id" in seen["where"]
        assert "org-1" in seen["where"]


@pytest.mark.asyncio
class TestWhatAMissSays:
    """The old message named neither what was looked for nor why it failed."""

    async def test_an_unknown_model_names_the_model(self):
        engine = _engine()
        engine.verify_api_key.return_value = _key()
        db, _ = _db_returning_nothing()

        got = await engine.resolve_context(db, "sk-1", "Qwen/Qwen2.5-1.5B-Instruct")

        assert got["valid"] is False
        assert "Qwen/Qwen2.5-1.5B-Instruct" in got["error"]

    async def test_an_unknown_model_names_the_type(self):
        """Asking for a chat model on the embeddings path fails the same way."""
        engine = _engine()
        engine.verify_api_key.return_value = _key()
        db, _ = _db_returning_nothing()

        got = await engine.resolve_context(db, "sk-1", "m", model_type="embedding")

        assert "embedding" in got["error"]

    async def test_a_dangling_bound_key_says_so_instead(self):
        """Nothing to do with the model, so the message must not mention one."""
        engine = _engine()
        engine.verify_api_key.return_value = _key(deployment_id=BOUND_ID)
        db, _ = _db_returning_nothing()

        got = await engine.resolve_context(db, "sk-1", "some-model")

        assert got["valid"] is False
        assert "some-model" not in got["error"]
        assert "no longer exists" in got["error"]

    async def test_an_invalid_key_is_still_reported_as_one(self):
        """The key check runs first; a bad key must not read as a bad model."""
        engine = _engine()
        engine.verify_api_key.return_value = None
        db, _ = _db_returning_nothing()

        got = await engine.resolve_context(db, "sk-bad", "m")

        assert got["valid"] is False
        assert "API Key" in got["error"]
