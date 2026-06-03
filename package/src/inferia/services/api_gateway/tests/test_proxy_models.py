"""Tests for model-cache proxy routes (Phase 7 wiring).

Covers:
  (a) GET /api/v1/models — forwards to v1/models, enforces MODEL_LIST.
  (b) POST /api/v1/models — enforces MODEL_ADD.
  (c) DELETE /api/v1/models/<id> — enforces MODEL_DELETE.
  (d) GET /hf/org/m/resolve/main/f — reaches orchestration unauthenticated
      (no 401/403), streamed.
  (e) GET /v2/org/m/blobs/sha — unauthenticated streaming passthrough.
  (f) 401 returned when MODEL_LIST permission is absent for GET /api/v1/models.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from httpx import AsyncClient, ASGITransport
from fastapi.responses import Response

# ---------------------------------------------------------------------------
# App + fixtures
# ---------------------------------------------------------------------------

from inferia.services.api_gateway.app import app
from inferia.services.api_gateway.rbac.auth import auth_service
from inferia.services.api_gateway.db.models import User as DBUser
from inferia.services.api_gateway.db.database import get_db

# Re-use conftest fixtures via import (pytest discovers them automatically, but
# we import them here for clarity and IDE resolution).
# The conftest.py `client` fixture injects full admin permissions.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fastapi_response(status_code: int, body: bytes, headers: dict | None = None):
    """Build a real fastapi.responses.Response so the route can return it directly."""
    return Response(
        content=body,
        status_code=status_code,
        headers=dict(headers or {"content-type": "application/json"}),
    )


def _make_mock_stream_ctx(status_code: int, body: bytes, headers: dict | None = None):
    """Build an async context manager that mimics client.stream(...)."""
    hdrs = dict(headers or {"content-type": "application/octet-stream"})

    class _FakeStream:
        def __init__(self):
            self.status_code = status_code
            self.headers = hdrs
            self._body = body

        async def aiter_bytes(self):
            yield self._body

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeStream()

        async def __aexit__(self, *args):
            pass

    return _FakeCtx()


# ---------------------------------------------------------------------------
# Tests — authenticated model management proxy (/api/v1/models)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_models_forwards_to_v1_models(client, admin_token):
    """GET /api/v1/models → proxy_request to orchestration v1/models."""
    mock_resp = _make_fastapi_response(200, b'{"models":[]}')

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.proxy_request",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ) as mock_pr:
        response = await client.get(
            "/api/v1/models/",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    # Gateway should have forwarded the request (200 from mock, not a 401/403).
    assert response.status_code == 200
    # Verify proxy_request was called with the v1/models path.
    mock_pr.assert_awaited_once()
    _, call_kwargs = mock_pr.call_args
    # path could be positional or keyword depending on call site
    call_args_pos = mock_pr.call_args.args
    call_args_kw = mock_pr.call_args.kwargs
    forwarded_path = call_args_kw.get("path") or (call_args_pos[1] if len(call_args_pos) > 1 else None)
    assert forwarded_path is not None
    assert "v1/models" in forwarded_path


@pytest.mark.asyncio
async def test_post_models_enforces_model_add(client, admin_token):
    """POST /api/v1/models → enforces MODEL_ADD permission."""
    mock_resp = _make_fastapi_response(202, b'{"status":"downloading","model_id":"x/y"}')

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.proxy_request",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        response = await client.post(
            "/api/v1/models/",
            json={"source": "hf", "model_id": "x/y"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    # Admin has model:add — should reach upstream.
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_delete_model_enforces_model_delete(client, admin_token):
    """DELETE /api/v1/models/<id> → enforces MODEL_DELETE permission."""
    mock_resp = _make_fastapi_response(204, b"")

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.proxy_request",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        response = await client.delete(
            "/api/v1/models/some-cache-id",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_get_models_requires_auth(client):
    """GET /api/v1/models without a token → 401 (auth middleware fires before RBAC)."""
    response = await client.get("/api/v1/models/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_models_requires_model_list_permission():
    """GET /api/v1/models with a token lacking model:list → 403."""
    # Create a guest user with no model permissions.
    guest_user = DBUser(id="user_guest_mc", email="guest_mc@inferia.com")
    guest_token = auth_service.create_access_token(
        guest_user, org_id="org_default", role="guest"
    )

    # Build a dedicated client where the middleware session returns a role
    # with empty permissions (simulating no model:list).
    mock_db_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [guest_user]
    mock_result.scalars.return_value.first.return_value = guest_user
    mock_db_session.execute = AsyncMock(return_value=mock_result)
    mock_db_session.close = AsyncMock()
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=None)

    middleware_session = AsyncMock()
    # A role with NO permissions.
    mock_role = MagicMock()
    mock_role.permissions = []
    mock_role_result = MagicMock()
    mock_role_result.scalars.return_value.all.return_value = [mock_role]
    middleware_session.execute = AsyncMock(return_value=mock_role_result)
    middleware_session.close = AsyncMock()
    middleware_session.__aenter__ = AsyncMock(return_value=middleware_session)
    middleware_session.__aexit__ = AsyncMock(return_value=None)
    mock_session_maker = MagicMock(return_value=middleware_session)

    app.dependency_overrides[get_db] = lambda: mock_db_session

    async def _mock_get_current_user(db, token):
        payload = auth_service.decode_token(token)
        user = DBUser(id=payload.sub, email=payload.sub + "@test.com")
        return user, "org_default", payload.roles

    with (
        patch.object(auth_service, "get_current_user", side_effect=_mock_get_current_user),
        patch(
            "inferia.services.api_gateway.rbac.middleware.AsyncSessionLocal",
            mock_session_maker,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            response = await ac.get(
                "/api/v1/models/",
                headers={"Authorization": f"Bearer {guest_token}"},
            )

    app.dependency_overrides.clear()
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests — unauthenticated streaming passthroughs (/hf, /v2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hf_passthrough_unauthenticated_no_auth_error(client):
    """GET /hf/org/m/resolve/main/f — no JWT needed; returns upstream body streamed."""
    upstream_body = b"fake-model-weights"

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, upstream_body)
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        # No Authorization header — should still succeed (not 401 or 403).
        response = await client.get("/hf/org/m/resolve/main/weights.bin")

    assert response.status_code == 200
    assert response.content == upstream_body


@pytest.mark.asyncio
async def test_hf_passthrough_forwards_range_header(client):
    """Range header is forwarded to upstream so engines can resume downloads."""
    upstream_body = b"partial-bytes"

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        stream_ctx = _make_mock_stream_ctx(
            206, upstream_body,
            headers={"content-type": "application/octet-stream", "content-range": "bytes 0-12/1000"},
        )
        mock_proxy.stream = MagicMock(return_value=stream_ctx)
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get(
            "/hf/org/m/resolve/main/weights.bin",
            headers={"Range": "bytes=0-12"},
        )

    assert response.status_code == 206
    # The Range header must have been passed to stream().
    call_kwargs = mock_proxy.stream.call_args.kwargs or {}
    call_headers = call_kwargs.get("headers", {})
    assert "range" in {k.lower() for k in call_headers}


@pytest.mark.asyncio
async def test_v2_passthrough_unauthenticated_no_auth_error(client):
    """GET /v2/org/m/blobs/sha256:abc — no JWT needed; streams upstream body."""
    upstream_body = b"oci-blob-content"

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, upstream_body)
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        # No auth header.
        response = await client.get("/v2/org/m/blobs/sha256:abc123")

    assert response.status_code == 200
    assert response.content == upstream_body


@pytest.mark.asyncio
async def test_hf_passthrough_upstream_503_propagated(client):
    """When the upstream returns 503, the gateway propagates that status."""
    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(503, b"upstream down")
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/hf/org/m/resolve/main/config.json")

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_hf_passthrough_network_error_returns_503(client):
    """When httpx raises RequestError, the gateway returns 503."""
    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream.side_effect = httpx.ConnectError("refused")
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/hf/org/m/resolve/main/config.json")

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Tests — SSRF path-traversal confinement (Fix 1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hf_path_traversal_percent_encoded_rejected(client):
    """GET /hf/%2e%2e/v1/nodes must return 400 — NOT reach orchestration.

    httpx ASGITransport decodes %2e%2e → ``..`` before passing path params to
    the handler, so the handler receives path='../v1/nodes', which posixpath
    resolves to '/v1/nodes' — outside the /hf/* prefix.
    The confinement check must reject this with HTTP 400.
    No upstream call is made (stream mock is never invoked).
    """
    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, b"should not reach here")
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/hf/%2e%2e/v1/nodes")

    assert response.status_code == 400
    # Verify the upstream was NOT contacted.
    mock_proxy.stream.assert_not_called()


@pytest.mark.asyncio
async def test_v2_path_traversal_percent_encoded_rejected(client):
    """GET /v2/%2e%2e/v1/nodes must return 400 — NOT reach orchestration."""
    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, b"should not reach here")
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/v2/%2e%2e/v1/nodes")

    assert response.status_code == 400
    mock_proxy.stream.assert_not_called()


@pytest.mark.asyncio
async def test_hf_normal_path_still_forwards(client):
    """GET /hf/org/m/resolve/main/file — normal path still forwarded (happy path).

    This complements the traversal-rejection test: confinement must only reject
    paths that escape the /hf/* prefix, not legitimate artifact fetches.
    """
    upstream_body = b"model-weights-bytes"

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, upstream_body)
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/hf/org/m/resolve/main/file.bin")

    assert response.status_code == 200
    assert response.content == upstream_body
    mock_proxy.stream.assert_called_once()


@pytest.mark.asyncio
async def test_v2_normal_path_still_forwards(client):
    """GET /v2/org/m/blobs/sha256:abc — normal path still forwarded."""
    upstream_body = b"oci-blob"

    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, upstream_body)
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        response = await client.get("/v2/org/m/blobs/sha256:abc123def456")

    assert response.status_code == 200
    assert response.content == upstream_body
    mock_proxy.stream.assert_called_once()


@pytest.mark.asyncio
async def test_hf_double_dot_literal_rejected(client):
    """GET /hf/../v1/nodes (literal double-dot) must also return 400.

    When Starlette receives the decoded path directly (e.g. some clients send
    literal double-dots without percent-encoding), posixpath.normpath must
    still escape the /hf/* prefix and the handler must reject it.
    """
    with patch(
        "inferia.services.api_gateway.gateway.proxy_routes.gateway_http_client"
    ) as mock_client_mgr:
        mock_proxy = MagicMock()
        mock_proxy.stream = MagicMock(
            return_value=_make_mock_stream_ctx(200, b"should not reach here")
        )
        mock_client_mgr.get_proxy_client.return_value = mock_proxy

        # Direct handler unit test: call proxy_hf_mirror with a path that
        # already contains double-dots. We invoke the handler logic indirectly
        # by patching the path parameter at the route level.
        from fastapi import HTTPException as _HTTPException
        from inferia.services.api_gateway.gateway.proxy_routes import proxy_hf_mirror
        import starlette.requests

        # Build a minimal request object sufficient for proxy_hf_mirror's
        # path-confinement check (which only needs the method).
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/hf/../v1/nodes",
            "query_string": b"",
            "headers": [],
        }
        req = starlette.requests.Request(scope)

        try:
            await proxy_hf_mirror(request=req, path="../v1/nodes")
            # If no exception raised, the test must fail.
            assert False, "Expected HTTPException(400) was not raised"
        except _HTTPException as exc:
            assert exc.status_code == 400

    mock_proxy.stream.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — exhaustive RBAC 405 branch (Fix 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_proxy_models_patch_returns_405(client, admin_token):
    """PATCH /api/v1/models/<id> — not in the declared methods; route returns 405."""
    # FastAPI itself rejects undeclared methods with 405 before the handler fires,
    # so this also validates the route declaration (GET, POST, DELETE only).
    response = await client.patch(
        "/api/v1/models/some-id",
        json={"key": "val"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 405
