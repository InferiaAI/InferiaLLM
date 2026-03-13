"""Tests for API key creation and verification security."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import cachetools

from inferia.services.api_gateway.rbac.auth import AuthService


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
    from inferia.services.api_gateway.policy.engine import PolicyEngine

    engine = PolicyEngine.__new__(PolicyEngine)
    engine.context_cache = cachetools.TTLCache(maxsize=100, ttl=10)
    engine.org_id_cache = cachetools.TTLCache(maxsize=100, ttl=300)
    engine.quota_policy_cache = cachetools.TTLCache(maxsize=100, ttl=60)
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

        asyncio.get_event_loop().run_until_complete(run())


class TestApiKeyOrgScoping:
    """API key context resolution is scoped to the key's org_id."""

    @pytest.mark.asyncio
    async def test_resolve_context_wrong_org_returns_invalid(self, policy_engine):
        """Key belonging to org-A cannot resolve context for org-B deployments."""
        from inferia.services.api_gateway.db.models import ApiKey as DBApiKey

        raw_key = "sk-abc123"
        from inferia.services.api_gateway.rbac.auth import auth_service

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
