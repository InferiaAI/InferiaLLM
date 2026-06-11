"""Tests for standardized API error classes — security layer."""

import pytest
from unittest.mock import patch

from inferia.common.errors import (
    APIError,
    BadRequestError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    RateLimitError,
    InternalServerError,
    ServiceUnavailableError,
    ErrorResponse,
    ErrorDetail,
)


class TestErrorStatusCodes:
    """Each error subclass returns correct HTTP status code."""

    @pytest.mark.parametrize(
        "error_cls,expected_status",
        [
            (BadRequestError, 400),
            (UnauthorizedError, 401),
            (ForbiddenError, 403),
            (NotFoundError, 404),
            (ConflictError, 409),
            (RateLimitError, 429),
            (InternalServerError, 500),
            (ServiceUnavailableError, 503),
        ],
    )
    def test_status_codes(self, error_cls, expected_status):
        err = error_cls()
        assert err.status_code == expected_status


class TestAPIErrorRequestId:
    """APIError injects request_id from context variable."""

    def test_request_id_injected(self):
        with patch("inferia.common.http_client.request_id_ctx") as mock_ctx:
            mock_ctx.get.return_value = "req-abc-123"
            err = BadRequestError(message="test")
            assert err.detail["request_id"] == "req-abc-123"

    def test_request_id_none_when_no_context(self):
        with patch("inferia.common.http_client.request_id_ctx") as mock_ctx:
            mock_ctx.get.return_value = None
            err = BadRequestError(message="test")
            assert err.detail["request_id"] is None


class TestRateLimitRetryAfter:
    """RateLimitError includes Retry-After header."""

    def test_default_retry_after(self):
        err = RateLimitError()
        assert err.headers["Retry-After"] == "60"

    def test_custom_retry_after(self):
        err = RateLimitError(retry_after=120)
        assert err.headers["Retry-After"] == "120"


class TestErrorResponseSerialization:
    """ErrorResponse Pydantic model matches API contract."""

    def test_serialization(self):
        resp = ErrorResponse(
            request_id="req-123",
            error=ErrorDetail(code="BAD_REQUEST", message="Invalid input"),
        )
        data = resp.model_dump()
        assert data["success"] is False
        assert data["request_id"] == "req-123"
        assert data["error"]["code"] == "BAD_REQUEST"
        assert data["error"]["message"] == "Invalid input"
        assert data["error"]["details"] is None


class TestDetailsParameter:
    """Details parameter (None vs dict) handled correctly."""

    def test_none_details(self):
        err = BadRequestError(message="test", details=None)
        assert err.detail["error"]["details"] == {}

    def test_dict_details(self):
        err = BadRequestError(message="test", details={"field": "email"})
        assert err.detail["error"]["details"] == {"field": "email"}
