"""Tests for API key creation and verification security."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import cachetools

from api_gateway.rbac.auth import AuthService


@pytest.fixture
def auth():
    svc = AuthService.__new__(AuthService)
    svc.secret_key = "test-secret-key-for-unit-tests-only"
    svc.algorithm = "HS256"
    svc.access_token_expire_minutes = 30
    svc.refresh_token_expire_days = 7
    return svc


@pytest.fixture
def policy_engine():
    from api_gateway.policy.engine import PolicyEngine

    engine = PolicyEngine.__new__(PolicyEngine)
    engine.context_cache = cachetools.TTLCache(maxsize=100, ttl=10)
    engine.org_id_cache = cachetools.TTLCache(maxsize=100, ttl=300)
    engine.quota_policy_cache = cachetools.TTLCache(maxsize=100, ttl=60)
    engine._cache_lock = asyncio.Lock()
    return engine


class TestApiKeyHashStorage:
    """API key creation must store only a hash, never the plaintext."""

    def test_hash_differs_from_plaintext(self, auth):
        raw_key = "sk-abc123def456"
        stored = auth.get_password_hash(raw_key)
        assert stored != raw_key

    def test_stored_value_is_bcrypt(self, auth):
        stored = auth.get_password_hash("sk-abc123def456")
        assert stored.startswith("$2")

    def test_correct_key_verifies(self, auth):
        raw = "sk-correct-key"
        stored = auth.get_password_hash(raw)
        assert auth.verify_password(raw, stored) is True

    def test_wrong_key_fails(self, auth):
        stored = auth.get_password_hash("sk-correct-key")
        assert auth.verify_password("sk-wrong-key", stored) is False

    def test_empty_key_returns_none(self, policy_engine):
        """verify_api_key returns None for empty key without hitting DB."""
        import asyncio

        async def run():
            mock_db = AsyncMock()
            result = await policy_engine.verify_api_key(mock_db, "")
            assert result is None
            mock_db.execute.assert_not_called()

        asyncio.run(run())


class TestApiKeyOrgScoping:
    """API key context resolution is scoped to the key's org_id."""

    @pytest.mark.asyncio
    async def test_resolve_context_wrong_org_returns_invalid(self, policy_engine):
        """Key belonging to org-A cannot resolve context for org-B deployments."""
        from api_gateway.db.models import ApiKey as DBApiKey

        raw_key = "sk-abc123"
        from api_gateway.rbac.auth import auth_service

        key_hash = auth_service.get_password_hash(raw_key)

        key_record = MagicMock(spec=DBApiKey)
        key_record.prefix = raw_key[:6] + "..."
        key_record.key_hash = key_hash
        key_record.org_id = "org-A"
        key_record.deployment_id = None

        mock_db = AsyncMock()

        # DB returns the key but no matching deployment (wrong org)
        key_result = MagicMock()
        key_result.scalars.return_value.all.return_value = [key_record]

        empty_result = MagicMock()
        empty_result.first.return_value = None

        mock_db.execute.side_effect = [key_result, empty_result]

        ctx = await policy_engine.resolve_context(mock_db, raw_key, "some-model")
        assert ctx["valid"] is False


def _sql(call) -> str:
    """The SQL of a db.execute call, with its values inlined."""
    stmt = call.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class TestRevokedKeys:
    """Revoking a key only clears is_active, so lookups must check it."""

    @pytest.mark.asyncio
    async def test_lookup_skips_inactive_keys(self, policy_engine):
        mock_db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = result

        await policy_engine.verify_api_key(mock_db, "sk-abc123")

        assert "is_active IS true" in _sql(mock_db.execute.call_args)


class TestBoundKeys:
    """A key bound to a deployment only ever serves its own org's deployment."""

    @pytest.mark.asyncio
    async def test_bound_lookup_is_scoped_to_the_key_org(self, policy_engine):
        import uuid
        from api_gateway.db.models import ApiKey as DBApiKey

        key_record = MagicMock(spec=DBApiKey)
        key_record.org_id = "org-A"
        key_record.deployment_id = uuid.uuid4()
        policy_engine.verify_api_key = AsyncMock(return_value=key_record)

        empty = MagicMock()
        empty.first.return_value = None
        mock_db = AsyncMock()
        mock_db.execute.return_value = empty

        ctx = await policy_engine.resolve_context(mock_db, "sk-abc123", "m")

        assert ctx["valid"] is False
        sql = _sql(mock_db.execute.call_args_list[0])
        assert "model_deployments.org_id = 'org-A'" in sql

    @pytest.mark.asyncio
    @pytest.mark.parametrize("deployment_id", [
        "0b5c7a3e-1111-4222-8333-944445555666",  # exists, but in another org
        "not-a-uuid",
    ])
    async def test_cannot_create_a_key_for_another_orgs_deployment(self, deployment_id):
        from fastapi import HTTPException
        from api_gateway.management import api_keys
        from api_gateway.schemas.management import ApiKeyCreate

        user_ctx = MagicMock(org_id="org-A", user_id="u1")
        no_row = MagicMock()
        no_row.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.add = MagicMock()
        db.execute.return_value = no_row

        with (
            patch.object(api_keys, "get_current_user_context", return_value=user_ctx),
            patch.object(api_keys.authz_service, "require_permission"),
            pytest.raises(HTTPException) as exc,
        ):
            await api_keys.create_api_key(
                ApiKeyCreate(name="k", deployment_id=deployment_id), MagicMock(), db
            )

        assert exc.value.status_code == 404
        db.add.assert_not_called()
