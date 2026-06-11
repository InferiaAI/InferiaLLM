"""
Tests for rate limit bypass via X-Forwarded-For header spoofing.

Verifies that the rate limiter uses request.client.host (ASGI-level IP)
and ignores the X-Forwarded-For header, which clients can spoof to
circumvent IP-based rate limiting.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from inferia.common.rate_limit import RateLimiter, login_rate_limiter


class TestRateLimiterIgnoresForwardedFor:
    """Verify that rate limiting uses real client IP, not X-Forwarded-For."""

    def test_spoofed_xff_does_not_bypass_rate_limit(self, client):
        """
        After exhausting rate limit from real IP, sending requests with a
        spoofed X-Forwarded-For must still be blocked.
        """
        import asyncio

        async def _run():
            # Reset global rate limiter state
            login_rate_limiter._store.clear()

            # Exhaust rate limit (5 attempts) from the ASGI client IP.
            # httpx ASGITransport uses 127.0.0.1 as client host.
            for i in range(6):
                resp = await client.post(
                    "/auth/login",
                    json={"username": "attacker@test.com", "password": "wrong"},
                )

            # 6th attempt should be rate-limited (429)
            assert resp.status_code == 429, (
                f"Expected 429 after 6 attempts, got {resp.status_code}"
            )

            # Now try with a spoofed X-Forwarded-For — should STILL be 429
            # because the server must ignore X-Forwarded-For.
            resp = await client.post(
                "/auth/login",
                json={"username": "attacker@test.com", "password": "wrong"},
                headers={"X-Forwarded-For": "203.0.113.99"},
            )
            assert resp.status_code == 429, (
                "Spoofed X-Forwarded-For bypassed rate limit! "
                f"Got {resp.status_code} instead of 429"
            )

        asyncio.get_event_loop().run_until_complete(_run())

    def test_different_xff_same_real_ip_shares_rate_limit(self, client):
        """
        Multiple requests with different X-Forwarded-For values from the
        same real IP must all count toward the same rate limit bucket.
        """
        import asyncio

        async def _run():
            login_rate_limiter._store.clear()

            fake_ips = [
                "10.0.0.1", "10.0.0.2", "10.0.0.3",
                "10.0.0.4", "10.0.0.5", "10.0.0.6",
            ]

            for fake_ip in fake_ips:
                resp = await client.post(
                    "/auth/login",
                    json={"username": "attacker@test.com", "password": "wrong"},
                    headers={"X-Forwarded-For": fake_ip},
                )

            # The 6th request should be rate-limited regardless of X-Forwarded-For
            assert resp.status_code == 429, (
                "Rotating X-Forwarded-For IPs bypassed rate limit! "
                f"Got {resp.status_code} instead of 429"
            )

        asyncio.get_event_loop().run_until_complete(_run())


class TestRateLimiterUnit:
    """Unit tests for the RateLimiter class itself."""

    def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            allowed, _ = limiter.is_allowed("user", "1.2.3.4")
            assert allowed

    def test_blocks_after_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("user", "1.2.3.4")

        allowed, retry_after = limiter.is_allowed("user", "1.2.3.4")
        assert not allowed
        assert retry_after is not None
        assert retry_after > 0

    def test_different_ips_have_separate_buckets(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        # Exhaust limit for IP A
        limiter.is_allowed("user", "1.1.1.1")
        limiter.is_allowed("user", "1.1.1.1")
        allowed_a, _ = limiter.is_allowed("user", "1.1.1.1")
        assert not allowed_a

        # IP B should still be allowed
        allowed_b, _ = limiter.is_allowed("user", "2.2.2.2")
        assert allowed_b

    def test_different_users_same_ip_separate_buckets(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("alice", "1.1.1.1")
        limiter.is_allowed("alice", "1.1.1.1")
        allowed_alice, _ = limiter.is_allowed("alice", "1.1.1.1")
        assert not allowed_alice

        allowed_bob, _ = limiter.is_allowed("bob", "1.1.1.1")
        assert allowed_bob

    def test_cleanup_removes_expired(self):
        import time

        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.is_allowed("user", "1.1.1.1")
        # Manually age the entry
        key = limiter._get_key("user", "1.1.1.1")
        limiter._store[key]["first_request"] = time.time() - 10
        limiter.cleanup()
        assert key not in limiter._store


class TestRateLimitDecoratorIgnoresXFF:
    """Verify the rate_limit_auth decorator does not use X-Forwarded-For."""

    def test_decorator_uses_client_host_not_xff(self):
        """The decorator must use request.client.host, not X-Forwarded-For."""
        from inferia.common.rate_limit import rate_limit_auth, RateLimiter
        from fastapi import FastAPI, Request
        from httpx import AsyncClient, ASGITransport
        import asyncio

        limiter = RateLimiter(max_requests=2, window_seconds=60)
        test_app = FastAPI()

        @test_app.post("/test")
        @rate_limit_auth(limiter, identifier_param="username")
        async def test_endpoint(request: Request, username: str = "test"):
            return {"status": "ok"}

        async def _run():
            async with AsyncClient(
                transport=ASGITransport(app=test_app, raise_app_exceptions=False),
                base_url="http://test",
            ) as ac:
                # Exhaust limit
                await ac.post("/test")
                await ac.post("/test")

                # 3rd should be blocked
                resp = await ac.post("/test")
                assert resp.status_code == 429

                # 4th with spoofed XFF should still be blocked
                resp = await ac.post(
                    "/test",
                    headers={"X-Forwarded-For": "99.99.99.99"},
                )
                assert resp.status_code == 429, (
                    "rate_limit_auth decorator allowed bypass via X-Forwarded-For"
                )

        asyncio.get_event_loop().run_until_complete(_run())
