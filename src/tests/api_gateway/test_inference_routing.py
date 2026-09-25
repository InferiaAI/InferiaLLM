"""Tests for inference context resolution and routing — Layer 3."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestResolveInferenceContext:
    """resolve_inference_context endpoint logic."""

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_not_valid(self):
        """Invalid API key returns valid=False response (not a 4xx raise)."""
        from api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="bad-key", model="llama-3")

        with patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(
                return_value={"valid": False, "error": "Invalid API Key"}
            )
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.valid is False
            assert "Invalid" in (response.error or "")

    @pytest.mark.asyncio
    async def test_valid_key_returns_deployment_context(self):
        """Valid key returns deployment configuration."""
        from api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="sk-valid-key", model="llama-3")

        resolved = {
            "valid": True,
            "deployment": {
                "id": "dep-001",
                "model_name": "llama-3",
                "endpoint": "http://llama:8080",
                "engine": "vllm",
                "configuration": "{}",
                "inference_model": "meta-llama/Llama-3-8b",
                "org_id": "org-001",
            },
            "config": {
                "rate_limit": None,
            },
            "user_id_context": "user-001",
            "log_payloads": True,
        }

        with patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(return_value=resolved)
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.valid is True
            assert response.deployment["model_name"] == "llama-3"
            assert response.deployment["endpoint"] == "http://llama:8080"

    @pytest.mark.asyncio
    async def test_rate_limit_config_forwarded(self):
        """rate_limit configuration is forwarded in the resolved context."""
        from api_gateway.gateway.router import (
            resolve_inference_context,
            ResolveContextRequest,
        )

        request = ResolveContextRequest(api_key="sk-valid-key", model="llama-3")

        resolved = {
            "valid": True,
            "deployment": {
                "id": "dep-001",
                "model_name": "llama-3",
                "endpoint": "http://llama:8080",
                "engine": "vllm",
                "configuration": "{}",
                "inference_model": None,
                "org_id": "org-001",
            },
            "config": {
                "rate_limit": {"enabled": True, "rpm": 60},
            },
            "user_id_context": "user-001",
            "log_payloads": False,
        }

        with patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_pe.resolve_context = AsyncMock(return_value=resolved)
            mock_db = AsyncMock()
            response = await resolve_inference_context(request, mock_db)
            assert response.rate_limit_config["enabled"] is True
            assert response.rate_limit_config["rpm"] == 60


class TestModelsList:
    """models list endpoint auth enforcement."""

    @pytest.mark.asyncio
    async def test_missing_bearer_token_returns_401(self):
        """Models list without Bearer token returns 401."""
        from api_gateway.gateway.router import list_models
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.headers = {}

        with patch(
            "api_gateway.gateway.router.rate_limiter"
        ) as mock_rl:
            mock_rl.check_rate_limit = AsyncMock()
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await list_models(mock_request, db=mock_db)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self):
        """Models list with invalid key returns 401."""
        from api_gateway.gateway.router import list_models
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer sk-bad-key"}

        with patch(
            "api_gateway.gateway.router.rate_limiter"
        ) as mock_rl, patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_rl.check_rate_limit = AsyncMock()
            mock_pe.verify_api_key = AsyncMock(return_value=None)
            mock_db = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await list_models(mock_request, db=mock_db)
            assert exc.value.status_code == 401

    @staticmethod
    def _key(org_id):
        record = MagicMock()
        record.org_id = org_id
        return record

    @staticmethod
    def _db_with_no_rows():
        """A session whose execute() is awaited but whose result is not:
        scalars() is sync, so it cannot be the AsyncMock default."""
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute.return_value = result
        return db

    @staticmethod
    def _emitted_sql(mock_db):
        """The SELECT the endpoint handed to the database, as text."""
        statement = mock_db.execute.call_args.args[0]
        return str(statement.compile(compile_kwargs={"literal_binds": True}))

    @pytest.mark.asyncio
    async def test_query_is_scoped_to_the_callers_org(self):
        """The filter lives in SQL, so this asserts on the emitted statement.
        Without it the endpoint returned every org's running deployments to
        any valid API key."""
        from api_gateway.gateway.router import list_models, models_cache

        models_cache.clear()
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer sk-good"}

        with patch("api_gateway.gateway.router.rate_limiter") as mock_rl, patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe, patch(
            "api_gateway.gateway.router.gateway_http_client"
        ):
            mock_rl.check_rate_limit = AsyncMock()
            mock_pe.verify_api_key = AsyncMock(return_value=self._key("org-alpha"))
            mock_db = self._db_with_no_rows()

            await list_models(mock_request, skip=0, limit=50, db=mock_db)

        sql = self._emitted_sql(mock_db)
        assert "org_id" in sql
        assert "org-alpha" in sql

    @pytest.mark.asyncio
    async def test_a_key_with_no_org_gets_nothing(self):
        """Otherwise the query matches `org_id IS NULL` and returns every
        orgless deployment."""
        from api_gateway.gateway.router import list_models, models_cache

        models_cache.clear()
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer sk-good"}

        with patch("api_gateway.gateway.router.rate_limiter") as mock_rl, patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe:
            mock_rl.check_rate_limit = AsyncMock()
            mock_pe.verify_api_key = AsyncMock(return_value=self._key(None))
            mock_db = AsyncMock()

            response = await list_models(mock_request, skip=0, limit=50, db=mock_db)

        assert response.data == []
        assert mock_db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_one_orgs_cached_response_is_not_served_to_another(self):
        """The 30s cache is shared process-wide, so its key carries the org."""
        from api_gateway.gateway.router import list_models, models_cache

        models_cache.clear()
        mock_request = MagicMock()
        mock_request.headers = {"Authorization": "Bearer sk-good"}

        with patch("api_gateway.gateway.router.rate_limiter") as mock_rl, patch(
            "api_gateway.gateway.router.policy_engine"
        ) as mock_pe, patch(
            "api_gateway.gateway.router.gateway_http_client"
        ):
            mock_rl.check_rate_limit = AsyncMock()
            mock_db = self._db_with_no_rows()

            mock_pe.verify_api_key = AsyncMock(return_value=self._key("org-alpha"))
            await list_models(mock_request, skip=0, limit=50, db=mock_db)

            mock_pe.verify_api_key = AsyncMock(return_value=self._key("org-beta"))
            await list_models(mock_request, skip=0, limit=50, db=mock_db)

        assert mock_db.execute.await_count == 2
        assert "org-beta" in self._emitted_sql(mock_db)
