"""Tests for deployment management — Layer 3 complex logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


def _make_user_ctx(org_id="org-001", user_id="user-001", permissions=None):
    ctx = MagicMock()
    ctx.org_id = org_id
    ctx.user_id = user_id
    ctx.permissions = permissions or ["deployment:create", "deployment:list", "deployment:delete"]
    return ctx


class TestCreateDeployment:
    """Deployment creation logic."""

    @pytest.mark.asyncio
    async def test_create_attaches_default_disabled_policies(self):
        """Creating a deployment attaches guardrail/rag/prompt_template policies (all disabled)."""
        from inferia.services.api_gateway.management.deployments import create_deployment
        from inferia.services.api_gateway.schemas.management import DeploymentCreate

        dep_data = DeploymentCreate(
            name="test-dep",
            model_name="llama-3",
            provider="openai",
            endpoint_url="https://api.openai.com/v1",
            credentials_json={},
        )
        mock_request = MagicMock()
        mock_db = AsyncMock()

        # DB flush/refresh return a mock deployment
        mock_dep = MagicMock()
        mock_dep.id = "dep-001"
        mock_dep.name = "test-dep"
        mock_dep.model_name = "llama-3"
        mock_dep.provider = "openai"
        mock_dep.endpoint_url = "https://api.openai.com/v1"
        mock_dep.credentials_json = None
        mock_dep.org_id = "org-001"
        mock_dep.state = "PENDING"
        mock_dep.created_at = None
        mock_dep.updated_at = None
        mock_dep.llmd_resource_name = None
        mock_dep.model_type = "inference"
        mock_db.refresh = AsyncMock(return_value=None)

        added_objects = []
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", "dep-001") or None)

        from inferia.services.api_gateway.db.models import Policy as DBPolicy

        with patch(
            "inferia.services.api_gateway.management.deployments.get_current_user_context",
            return_value=_make_user_ctx(),
        ), patch(
            "inferia.services.api_gateway.management.deployments.authz_service"
        ), patch(
            "inferia.services.api_gateway.audit.service.audit_service"
        ) as mock_audit:
            mock_audit.log_event = AsyncMock()

            result = await create_deployment(dep_data, mock_request, mock_db)

            # Three default policies should have been added
            policy_objects = [o for o in added_objects if isinstance(o, DBPolicy)]
            policy_types = {p.policy_type for p in policy_objects}
            assert "guardrail" in policy_types
            assert "rag" in policy_types
            assert "prompt_template" in policy_types

            # All disabled by default
            for p in policy_objects:
                assert p.config_json["enabled"] is False

    @pytest.mark.asyncio
    async def test_create_without_org_raises_400(self):
        """Creating a deployment without org context raises 400."""
        from inferia.services.api_gateway.management.deployments import create_deployment
        from inferia.services.api_gateway.schemas.management import DeploymentCreate

        dep_data = DeploymentCreate(
            name="test-dep",
            model_name="llama-3",
            provider="openai",
            endpoint_url="https://api.openai.com/v1",
            credentials_json={},
        )
        mock_request = MagicMock()
        mock_db = AsyncMock()

        with patch(
            "inferia.services.api_gateway.management.deployments.get_current_user_context",
            return_value=_make_user_ctx(org_id=None),
        ), patch(
            "inferia.services.api_gateway.management.deployments.authz_service"
        ):
            with pytest.raises(HTTPException) as exc:
                await create_deployment(dep_data, mock_request, mock_db)
            assert exc.value.status_code == 400


class TestListDeployments:
    """Deployment listing is scoped to caller's org."""

    @pytest.mark.asyncio
    async def test_list_returns_only_caller_org(self):
        """list_deployments filters by org_id from user context."""
        from inferia.services.api_gateway.management.deployments import list_deployments

        mock_request = MagicMock()
        mock_db = AsyncMock()

        mock_dep = MagicMock()
        mock_dep.org_id = "org-001"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_dep]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "inferia.services.api_gateway.management.deployments.get_current_user_context",
            return_value=_make_user_ctx(org_id="org-001"),
        ), patch(
            "inferia.services.api_gateway.management.deployments.authz_service"
        ):
            result = await list_deployments(mock_request, skip=0, limit=50, db=mock_db)
            assert len(result) == 1
            assert result[0].org_id == "org-001"

    @pytest.mark.asyncio
    async def test_list_without_org_returns_empty(self):
        """list_deployments with no org context returns empty list."""
        from inferia.services.api_gateway.management.deployments import list_deployments

        mock_request = MagicMock()
        mock_db = AsyncMock()

        with patch(
            "inferia.services.api_gateway.management.deployments.get_current_user_context",
            return_value=_make_user_ctx(org_id=None),
        ), patch(
            "inferia.services.api_gateway.management.deployments.authz_service"
        ):
            result = await list_deployments(mock_request, db=mock_db)
            assert result == []
            mock_db.execute.assert_not_called()


class TestDeleteDeployment:
    """Deployment deletion via management API."""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_deployment_raises_404(self):
        """Deleting a deployment that doesn't exist raises 404."""
        from inferia.services.api_gateway.management.deployments import delete_deployment

        mock_request = MagicMock()
        mock_db = AsyncMock()

        empty_result = MagicMock()
        empty_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(return_value=empty_result)

        with patch(
            "inferia.services.api_gateway.management.deployments.get_current_user_context",
            return_value=_make_user_ctx(),
        ), patch(
            "inferia.services.api_gateway.management.deployments.authz_service"
        ):
            with pytest.raises(HTTPException) as exc:
                await delete_deployment("non-existent-id", mock_request, mock_db)
            assert exc.value.status_code == 404
