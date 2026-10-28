"""State every organization shares can only be changed by the superadmin, and
no one can grant a role stronger than their own.

Roles and provider credentials are single install-wide records, and the model
cache serves every org, so an org admin changing them changes every org.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api_gateway.models import UserContext
from api_gateway.rbac.authorization import authz_service

SUPERADMIN = "root@example.com"


@pytest.fixture
def superadmin_email(monkeypatch):
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "superadmin_email", SUPERADMIN)


def _ctx(email=SUPERADMIN, local=True, permissions=None):
    return UserContext(
        user_id="u1", username=email, email=email, roles=["admin"],
        permissions=permissions or [], org_id="org-a", quota_limit=1, quota_used=0,
        local_session=local,
    )


class TestWhoIsTheSuperadmin:

    def test_the_superadmin_on_a_local_login(self, superadmin_email):
        assert authz_service.is_superadmin(_ctx())

    def test_the_email_is_matched_in_any_case(self, superadmin_email):
        assert authz_service.is_superadmin(_ctx(email=" Root@Example.com"))

    def test_an_sso_session_with_the_same_email_is_not(self, superadmin_email):
        """SSO users map to local users by email, so an IdP account carrying
        the superadmin's address must not count."""
        assert not authz_service.is_superadmin(_ctx(local=False))

    def test_another_user_is_not(self, superadmin_email):
        assert not authz_service.is_superadmin(_ctx(email="admin@example.com"))

    def test_no_superadmin_configured_means_no_one(self, monkeypatch):
        from api_gateway.config import settings

        monkeypatch.setattr(settings, "superadmin_email", None)
        assert not authz_service.is_superadmin(_ctx())


class TestGrantingRoles:

    def test_cannot_grant_a_permission_you_lack(self, superadmin_email):
        member = _ctx(email="m@example.com", permissions=["deployment:list"])
        with pytest.raises(HTTPException) as exc:
            authz_service.require_grantable(member, ["deployment:list", "role:update"])
        assert exc.value.status_code == 403

    def test_can_grant_a_subset_of_your_own(self, superadmin_email):
        admin = _ctx(email="a@example.com", permissions=["deployment:list", "role:update"])
        authz_service.require_grantable(admin, ["deployment:list"])

    def test_the_superadmin_can_grant_anything(self, superadmin_email):
        authz_service.require_grantable(_ctx(), ["role:update", "org:update"])

    @pytest.mark.asyncio
    async def test_an_unknown_role_is_a_400(self, superadmin_email):
        none = MagicMock()
        none.scalars.return_value.first.return_value = None
        db = AsyncMock()
        db.execute.return_value = none
        with pytest.raises(HTTPException) as exc:
            await authz_service.require_grantable_role(db, _ctx(), "no-such-role")
        assert exc.value.status_code == 400


# --- over HTTP, as an org admin who holds every permission but is not the superadmin


@pytest.fixture
def org_admin(monkeypatch):
    """The test client's admin token, with SUPERADMIN_EMAIL set to someone else."""
    from api_gateway.config import settings

    monkeypatch.setattr(settings, "superadmin_email", SUPERADMIN)


@pytest.mark.asyncio
async def test_an_org_admin_cannot_edit_a_shared_role(client, admin_token, org_admin):
    r = await client.put(
        "/admin/roles/member", json={"permissions": ["admin:all"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_an_org_admin_cannot_change_provider_credentials(client, admin_token, org_admin):
    r = await client.post(
        "/management/config/providers/huggingface/credentials",
        json={"name": "x", "credential_type": "token", "value": "hf_x"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_an_org_admin_cannot_delete_a_cached_model(client, admin_token, org_admin):
    with patch("api_gateway.gateway.proxy_routes.proxy_request", new_callable=AsyncMock) as pr:
        r = await client.delete(
            "/v1/models/some-cache-id", headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert r.status_code == 403
    pr.assert_not_called()


@pytest.mark.asyncio
async def test_audit_logs_for_a_caller_with_no_org_are_empty():
    """The service skips the org filter when none is given."""
    from api_gateway.audit import router as audit_router

    caller = _ctx(permissions=["audit_log:list"])
    caller.org_id = None
    with (
        patch.object(audit_router.authz_service, "require_permission"),
        patch.object(audit_router.audit_service, "get_logs", AsyncMock()) as get_logs,
    ):
        out = await audit_router.get_audit_logs(db=AsyncMock(), user_ctx=caller)
    assert out == []
    get_logs.assert_not_called()


@pytest.mark.asyncio
async def test_a_user_with_no_org_cannot_log_in():
    """Login used to join such a user to the first org, which is the main tenant."""
    from api_gateway.db.models import User as DBUser
    from api_gateway.rbac.auth import auth_service

    user = DBUser(id="u-orphan", email="o@example.com", password_hash="h")
    no_orgs = MagicMock()
    no_orgs.all.return_value = []
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = no_orgs
    request = MagicMock(username="o@example.com", password="pw")
    with patch.object(auth_service, "authenticate_user", AsyncMock(return_value=user)):
        with pytest.raises(HTTPException) as exc:
            await auth_service.login(db, request)
    assert exc.value.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_auth_me_reports_whether_the_caller_is_the_superadmin(client, admin_token, monkeypatch):
    """The dashboard hides superadmin-only controls from everyone else."""
    from api_gateway.config import settings

    auth = {"Authorization": f"Bearer {admin_token}"}
    monkeypatch.setattr(settings, "superadmin_email", "user_admin_001@test.com")
    yes = await client.get("/auth/me", headers=auth)
    monkeypatch.setattr(settings, "superadmin_email", SUPERADMIN)
    no = await client.get("/auth/me", headers=auth)

    assert yes.status_code == 200 and yes.json()["is_superadmin"] is True
    assert no.status_code == 200 and no.json()["is_superadmin"] is False
