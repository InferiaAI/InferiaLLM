"""
Circuit Breaker Pattern Implementation
Prevents cascading failures when external services are down.
"""

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Callable, Optional, TypeVar, Generic, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation - requests pass through
    OPEN = "open"  # Failure threshold reached - requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""

    def __init__(self, message: str = "Circuit breaker is open"):
        self.message = message
        super().__init__(self.message)


class CircuitBreaker:
    """
    Circuit breaker implementation for external service calls.

    Usage:
        breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception
        )

        @breaker
        async def call_external_service():
            # Your code here
            pass
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        expected_exception: type = Exception,
        name: str = "default",
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying half-open
            expected_exception: Exception type(s) to count as failures
            name: Circuit breaker name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state

    async def _can_execute(self) -> bool:
        """Check if request can be executed."""
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if (
                    self._last_failure_time
                    and (time.time() - self._last_failure_time) >= self.recovery_timeout
                ):
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        f"Circuit breaker '{self.name}' entering half-open state"
                    )
                    return True
                return False

            # HALF_OPEN - allow one request to test
            return True

    async def _record_success(self):
        """Record successful request."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_failure_time = None
                logger.info(f"Circuit breaker '{self.name}' closed - service recovered")
            else:
                self._failure_count = 0

    async def _record_failure(self):
        """Record failed request."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Failed in half-open, go back to open
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker '{self.name}' reopened - recovery failed"
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    f"Circuit breaker '{self.name}' opened after {self._failure_count} failures"
                )

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to wrap function with circuit breaker.

        Can be used as:
            @breaker
            async def my_function():
                pass
        """

        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            if not await self._can_execute():
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN - service unavailable"
                )

            try:
                result = await func(*args, **kwargs)
                await self._record_success()
                return result
            except self.expected_exception as e:
                await self._record_failure()
                raise

        return wrapper

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        pass


class CircuitBreakerRegistry:
    """Registry to manage multiple circuit breakers."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        expected_exception: type = Exception,
    ) -> CircuitBreaker:
        """Get existing circuit breaker or create new one."""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=expected_exception,
                name=name,
            )
        return self._breakers[name]

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        return self._breakers.get(name)

    def status(self) -> dict:
        """Get status of all circuit breakers."""
        return {
            name: {
                "state": breaker.state.value,
                "failure_count": breaker._failure_count,
            }
            for name, breaker in self._breakers.items()
        }


# Global registry
circuit_breaker_registry = CircuitBreakerRegistry()


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    expected_exception: type = Exception,
):
    """
    Decorator to add circuit breaker to a function.

    Usage:
        @circuit_breaker("redis", failure_threshold=3)
        async def get_from_redis(key):
            return await redis.get(key)
    """
    breaker = circuit_breaker_registry.get_or_create(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        expected_exception=expected_exception,
    )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        return breaker(func)

    return decorator
