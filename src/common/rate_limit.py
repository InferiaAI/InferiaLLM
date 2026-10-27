"""
Rate limiting utilities for authentication endpoints.
Provides IP-based and username-based rate limiting.
"""

import time
import logging
from functools import wraps
from typing import Optional
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple in-memory rate limiter for authentication endpoints."""

    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: int = 60,
        block_duration_seconds: int = 300,
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds
            block_duration_seconds: How long to block after exceeding limit
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.block_duration_seconds = block_duration_seconds

        # Store: key -> {count: int, first_request: float, blocked_until: Optional[float]}
        self._store = {}
        self._last_cleanup = 0.0

    def _get_key(self, identifier: str, ip_address: str) -> str:
        """Create a composite key from identifier and IP."""
        return f"{identifier}:{ip_address}"

    def is_allowed(
        self, identifier: str, ip_address: str
    ) -> tuple[bool, Optional[int]]:
        """
        Check if request is allowed.

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        key = self._get_key(identifier, ip_address)
        now = time.time()

        # Every new username/IP pair adds an entry, so prune once per window.
        if now - self._last_cleanup > self.window_seconds:
            self.cleanup()
            self._last_cleanup = now

        # Get or create entry
        entry = self._store.get(
            key, {"count": 0, "first_request": now, "blocked_until": None}
        )

        # Check if currently blocked
        if entry["blocked_until"] and now < entry["blocked_until"]:
            retry_after = int(entry["blocked_until"] - now)
            return False, retry_after

        # Reset if window has passed
        if now - entry["first_request"] > self.window_seconds:
            entry = {"count": 1, "first_request": now, "blocked_until": None}
            self._store[key] = entry
            return True, None

        # Check if limit exceeded
        if entry["count"] >= self.max_requests:
            # Block the client
            entry["blocked_until"] = now + self.block_duration_seconds
            self._store[key] = entry
            logger.warning(f"Rate limit exceeded for {identifier} from {ip_address}")
            return False, self.block_duration_seconds

        # Increment count
        entry["count"] += 1
        self._store[key] = entry
        return True, None

    def blocked_for(self, identifier: str, ip_address: str) -> Optional[int]:
        """Seconds left on a block, or None. Counts nothing, unlike is_allowed."""
        entry = self._store.get(self._get_key(identifier, ip_address))
        now = time.time()
        if entry and entry["blocked_until"] and now < entry["blocked_until"]:
            return int(entry["blocked_until"] - now) + 1
        return None

    def cleanup(self):
        """Clean up expired entries."""
        now = time.time()
        expired_keys = [
            key
            for key, entry in self._store.items()
            if not (entry["blocked_until"] and now < entry["blocked_until"])
            and now - entry["first_request"] > self.window_seconds
        ]
        for key in expired_keys:
            del self._store[key]


# Global rate limiters for different auth endpoints
login_rate_limiter = RateLimiter(
    max_requests=5,  # 5 login attempts
    window_seconds=60,  # per 60 seconds
    block_duration_seconds=300,  # block for 5 minutes
)

# Failed logins per account from any address. Caps password and 2FA guessing
# even when the client IP can be faked or shared behind a proxy. Anyone who
# knows an email can still hold that account blocked, so the block is short.
login_account_limiter = RateLimiter(
    max_requests=20,
    window_seconds=60,
    block_duration_seconds=60,
)

register_rate_limiter = RateLimiter(
    max_requests=3,  # 3 registration attempts
    window_seconds=3600,  # per hour
    block_duration_seconds=3600,  # block for 1 hour
)


def rate_limit_auth(limiter: RateLimiter, identifier_param: str = "username"):
    """
    Decorator to add rate limiting to authentication endpoints.

    Usage:
        @router.post("/login")
        @rate_limit_auth(login_rate_limiter, identifier_param="username")
        async def login(request: Request, ...):
            pass
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request from kwargs or args
            request: Optional[Request] = kwargs.get("request")
            if not request and args:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if not request:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Rate limiting requires Request parameter",
                )

            # Get identifier (usually username/email)
            identifier = kwargs.get(identifier_param, "")
            if not identifier and "request" in kwargs:
                # Try to get from request body
                body = kwargs["request"]
                if hasattr(body, identifier_param):
                    identifier = getattr(body, identifier_param, "")

            # Get client IP — use ASGI-level client IP only.
            # Never trust X-Forwarded-For from the client; configure
            # uvicorn with --proxy-headers for reverse proxy setups.
            client_ip = request.client.host if request.client else "unknown"

            # Check rate limit
            is_allowed, retry_after = limiter.is_allowed(
                identifier or "anonymous", client_ip
            )

            if not is_allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many requests. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)},
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
