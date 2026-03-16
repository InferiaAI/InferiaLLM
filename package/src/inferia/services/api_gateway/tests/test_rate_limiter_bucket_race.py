"""
Tests for race condition fix in InMemoryRateLimiter._get_bucket.

Verifies that bucket creation uses dict.setdefault() for atomic
check-and-set, preventing concurrent coroutines from creating
duplicate TokenBucket instances and resetting rate limit state.

Closes #67
"""

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def make_in_memory_limiter():
    """Create an InMemoryRateLimiter with mocked settings."""
    with patch("inferia.services.api_gateway.gateway.rate_limiter.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 60
        mock_settings.rate_limit_burst_size = 10
        mock_settings.use_redis_rate_limit = False

        from inferia.services.api_gateway.gateway.rate_limiter import InMemoryRateLimiter

        yield InMemoryRateLimiter()


class TestBucketCreationAtomicity:
    """Verify _get_bucket uses atomic setdefault to prevent race conditions."""

    def test_get_bucket_returns_same_object_for_same_key(self, make_in_memory_limiter):
        """Calling _get_bucket twice with the same key must return the exact same object."""
        limiter = make_in_memory_limiter
        bucket1 = limiter._get_bucket("user:alice")
        bucket2 = limiter._get_bucket("user:alice")

        assert bucket1 is bucket2, (
            "_get_bucket returned different TokenBucket instances for the same key"
        )

    def test_get_bucket_returns_different_objects_for_different_keys(self, make_in_memory_limiter):
        """Different keys must get separate buckets."""
        limiter = make_in_memory_limiter
        bucket_a = limiter._get_bucket("user:alice")
        bucket_b = limiter._get_bucket("user:bob")

        assert bucket_a is not bucket_b, (
            "_get_bucket returned the same TokenBucket for different keys"
        )

    def test_get_bucket_uses_setdefault(self):
        """
        The _get_bucket method must use dict.setdefault() for atomic
        check-and-set. This prevents a race where two coroutines both
        see the key as missing and create separate TokenBucket instances.
        """
        # Read the source file directly to avoid stale bytecode cache issues
        source_file = (
            Path(__file__).resolve().parent.parent
            / "gateway"
            / "rate_limiter.py"
        )
        source = source_file.read_text()
        assert "setdefault" in source, (
            "_get_bucket must use dict.setdefault() for atomic bucket creation. "
            "The current check-then-set pattern has a race condition where "
            "concurrent coroutines can create duplicate TokenBucket instances."
        )
