"""
Tests for rate limiter user identification source.

Verifies that the gateway rate limiter reads user_id from the authenticated
JWT context (request.state.user.user_id) rather than the client-controlled
request.state.user_id header value.

Closes #41
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def make_rate_limiter():
    """Create a RateLimiter instance with mocked limiter backend."""
    with patch("inferia.services.api_gateway.gateway.rate_limiter.settings") as mock_settings:
        mock_settings.rate_limit_enabled = True
        mock_settings.rate_limit_requests_per_minute = 100
        mock_settings.rate_limit_burst_size = 50
        mock_settings.use_redis_rate_limit = False

        from inferia.services.api_gateway.gateway.rate_limiter import RateLimiter

        rl = RateLimiter()
        rl.limiter = MagicMock()
        rl.limiter.is_allowed = AsyncMock(
            return_value=(True, {"limit": 100, "remaining": 99, "reset": 0})
        )
        yield rl


def _make_request(user=None, user_id=None, client_host="192.168.1.1"):
    """Build a mock Request with optional user context and user_id header value."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_host

    state = MagicMock(spec=[])
    if user is not None:
        state.user = user
    if user_id is not None:
        state.user_id = user_id

    # Make getattr work correctly on the mock state
    original_attrs = {}
    if user is not None:
        original_attrs["user"] = user
    if user_id is not None:
        original_attrs["user_id"] = user_id

    def side_effect(attr, default=None):
        return original_attrs.get(attr, default)

    # We need getattr to work on request.state, so we use a real object
    class FakeState:
        pass

    fake_state = FakeState()
    if user is not None:
        fake_state.user = user
    if user_id is not None:
        fake_state.user_id = user_id

    request.state = fake_state
    return request


class TestRateLimiterUsesAuthenticatedUser:
    """Verify rate limiter derives user_id from JWT-authenticated context."""

    @pytest.mark.asyncio
    async def test_uses_user_from_jwt_context(self, make_rate_limiter):
        """Rate limiter must use request.state.user.user_id (JWT) as the key."""
        rl = make_rate_limiter

        user_ctx = MagicMock()
        user_ctx.user_id = "jwt-authenticated-user-42"

        request = _make_request(user=user_ctx)
        await rl.check_rate_limit(request)

        rl.limiter.is_allowed.assert_awaited_once()
        key_used = rl.limiter.is_allowed.call_args[0][0]
        assert key_used == "user:jwt-authenticated-user-42", (
            f"Expected key 'user:jwt-authenticated-user-42', got '{key_used}'"
        )

    @pytest.mark.asyncio
    async def test_spoofed_user_id_header_ignored_when_no_user(self, make_rate_limiter):
        """
        When request.state.user is not set, a spoofed request.state.user_id
        must be ignored and IP-based limiting must be used instead.
        """
        rl = make_rate_limiter

        # Attacker sets user_id on request.state (from X-User-ID header)
        # but has no authenticated user context
        request = _make_request(user=None, user_id="spoofed-admin-id", client_host="10.0.0.99")
        await rl.check_rate_limit(request)

        rl.limiter.is_allowed.assert_awaited_once()
        key_used = rl.limiter.is_allowed.call_args[0][0]
        assert key_used == "ip:10.0.0.99", (
            f"Expected IP-based key 'ip:10.0.0.99', got '{key_used}'. "
            "Rate limiter is reading user_id from header instead of JWT!"
        )

    @pytest.mark.asyncio
    async def test_ip_based_limiting_when_no_user_authenticated(self, make_rate_limiter):
        """When no user is authenticated at all, IP-based limiting must be used."""
        rl = make_rate_limiter

        request = _make_request(user=None, user_id=None, client_host="203.0.113.50")
        await rl.check_rate_limit(request)

        rl.limiter.is_allowed.assert_awaited_once()
        key_used = rl.limiter.is_allowed.call_args[0][0]
        assert key_used == "ip:203.0.113.50", (
            f"Expected IP-based key 'ip:203.0.113.50', got '{key_used}'"
        )
