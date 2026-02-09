"""
Standardized error response format for API endpoints.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel
from fastapi import HTTPException, status


class ErrorDetail(BaseModel):
    """Standardized error detail structure."""

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standardized API error response."""

    success: bool = False
    error: ErrorDetail


class APIError(HTTPException):
    """Base class for API errors with standardized format."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.error_code = code
        self.error_message = message
        self.error_details = details

        error_response = {
            "success": False,
            "error": {"code": code, "message": message, "details": details or {}},
        }

        super().__init__(
            status_code=status_code, detail=error_response, headers=headers
        )


class BadRequestError(APIError):
    """400 Bad Request error."""

    def __init__(
        self,
        message: str = "Bad request",
        code: str = "BAD_REQUEST",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=code,
            message=message,
            details=details,
        )


class UnauthorizedError(APIError):
    """401 Unauthorized error."""

    def __init__(
        self,
        message: str = "Unauthorized",
        code: str = "UNAUTHORIZED",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=code,
            message=message,
            details=details,
        )


class ForbiddenError(APIError):
    """403 Forbidden error."""

    def __init__(
        self,
        message: str = "Forbidden",
        code: str = "FORBIDDEN",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code=code,
            message=message,
            details=details,
        )


class NotFoundError(APIError):
    """404 Not Found error."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "NOT_FOUND",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code=code,
            message=message,
            details=details,
        )


class ConflictError(APIError):
    """409 Conflict error."""

    def __init__(
        self,
        message: str = "Conflict",
        code: str = "CONFLICT",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
            details=details,
        )


class RateLimitError(APIError):
    """429 Rate Limit Exceeded error."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        code: str = "RATE_LIMIT_EXCEEDED",
        details: Optional[Dict] = None,
        retry_after: int = 60,
    ):
        headers = {"Retry-After": str(retry_after)}
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=code,
            message=message,
            details=details,
            headers=headers,
        )


class InternalServerError(APIError):
    """500 Internal Server Error."""

    def __init__(
        self,
        message: str = "Internal server error",
        code: str = "INTERNAL_ERROR",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=code,
            message=message,
            details=details,
        )


class ServiceUnavailableError(APIError):
    """503 Service Unavailable error (e.g., circuit breaker open)."""

    def __init__(
        self,
        message: str = "Service temporarily unavailable",
        code: str = "SERVICE_UNAVAILABLE",
        details: Optional[Dict] = None,
    ):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=code,
            message=message,
            details=details,
        )
