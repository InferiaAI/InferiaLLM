"""A worker shell is a root shell, so it needs its own permission.

Members get DEPLOYMENT_UPDATE by default, which the shell used to require.
NODE_SHELL is admin-only in every auth mode: local admins hold every
permission, and in the catalog modes it comes only from the admin-only
``inferiallm:org:write`` key.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_gateway.app import app
from api_gateway.gateway import proxy_routes
from api_gateway.models import UserContext
from api_gateway.rbac.catalog import CATALOG
from api_gateway.rbac.initialization import MEMBER_PERMISSIONS
from api_gateway.rbac.permissions import (
    canonical_permissions,
    expand_catalog_permissions,
)
from api_gateway.schemas.auth import PermissionEnum

SHELL = PermissionEnum.NODE_SHELL.value


def _catalog_role(name):
    return next(r for r in CATALOG.roles if r.name == name)


def test_local_admins_hold_it_and_members_do_not():
    assert SHELL in canonical_permissions()
    assert SHELL not in MEMBER_PERMISSIONS


def test_catalog_admins_hold_it():
    assert SHELL in expand_catalog_permissions(_catalog_role("admin").permissions)


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_catalog_non_admins_do_not(role):
    assert SHELL not in expand_catalog_permissions(_catalog_role(role).permissions)


def _user(*permissions):
    return UserContext(
        user_id="u1", username="a@b.com", email="a@b.com", roles=["member"],
        permissions=list(permissions), org_id="org1",
        quota_limit=10000, quota_used=0,
    )


def _no_upstream(*_a, **_kw):
    raise OSError("no orchestration in this test")


@pytest.fixture
def connect_shell(monkeypatch):
    def _connect(user):
        async def _resolve(_token):
            return user

        monkeypatch.setattr(proxy_routes, "_get_ws_user_context", _resolve)
        # Replace the module reference, not its attribute: another test swaps
        # the websockets package for a stub, which breaks its lazy imports.
        monkeypatch.setattr(
            proxy_routes, "websockets", SimpleNamespace(connect=_no_upstream),
        )
        with TestClient(app).websocket_connect(
            "/v1/admin/workers/3f6c2a4e-1d2b-4c5e-8f9a-0b1c2d3e4f50/shell?access_token=t",
        ):
            pass

    return _connect


def test_deployment_update_alone_no_longer_opens_a_shell(connect_shell):
    with pytest.raises(WebSocketDisconnect):
        connect_shell(_user(PermissionEnum.DEPLOYMENT_UPDATE.value))


def test_the_shell_permission_gets_past_the_gateway(connect_shell):
    # Accepted; the relay then fails only because no orchestration runs here.
    connect_shell(_user(SHELL))
