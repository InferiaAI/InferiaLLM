"""Tests for auth middleware invitation path bypass (issue #63).

The middleware must only skip auth for the specific public invitation
lookup route, not for any arbitrary path under /auth/invitations/.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from inferia.services.api_gateway.rbac.middleware import auth_middleware


def make_request(path: str, method: str = "GET") -> MagicMock:
    """Create a mock Request with the given path."""
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = {}  # no auth header
    return request


class TestInvitationPathBypass:

    @pytest.mark.asyncio
    async def test_invite_lookup_is_public(self):
        """GET /auth/invitations/{token} should bypass auth."""
        request = make_request("/auth/invitations/abc123token")
        call_next = AsyncMock(return_value=MagicMock())

        response = await auth_middleware(request, call_next)

        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_nested_path_under_invitations_requires_auth(self):
        """Paths like /auth/invitations/{token}/accept must NOT bypass auth."""
        request = make_request("/auth/invitations/abc123/accept")
        call_next = AsyncMock(return_value=MagicMock())

        response = await auth_middleware(request, call_next)

        # Should NOT have called through — should return 401 (no auth header)
        call_next.assert_not_called()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_deep_nested_path_requires_auth(self):
        """Paths like /auth/invitations/x/y/z must NOT bypass auth."""
        request = make_request("/auth/invitations/token/some/deep/path")
        call_next = AsyncMock(return_value=MagicMock())

        response = await auth_middleware(request, call_next)

        call_next.assert_not_called()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invitations_list_requires_auth(self):
        """GET /auth/invitations/ (trailing slash, no token) must NOT bypass auth."""
        request = make_request("/auth/invitations/")
        call_next = AsyncMock(return_value=MagicMock())

        response = await auth_middleware(request, call_next)

        # This is a bare prefix with no token — should require auth
        # (the actual invite lookup needs a token segment)
        call_next.assert_not_called()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_known_public_paths_still_work(self):
        """Existing public paths should continue to bypass auth."""
        call_next = AsyncMock(return_value=MagicMock())

        for path in ["/auth/login", "/auth/register", "/health", "/auth/refresh"]:
            request = make_request(path)
            await auth_middleware(request, call_next)

        assert call_next.call_count == 4
