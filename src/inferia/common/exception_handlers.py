"""
Standard exception handlers for FastAPI applications.
"""

import logging
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from inferia.common.errors import APIError, InternalServerError
from inferia.common.http_client import request_id_ctx

logger = logging.getLogger(__name__)

async def api_error_handler(request: Request, exc: APIError):
    """Handler for standardized APIError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
        headers=exc.headers,
    )

async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Handler for FastAPI/Pydantic validation errors."""
    error_response = {
        "success": False,
        "request_id": request_id_ctx.get(),
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Validation failed for the request",
            "details": {"errors": exc.errors()},
        },
    }
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response,
    )

async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all handler for unhandled exceptions (500 Internal Server Error)."""
    logger.exception(f"Unhandled exception occurred: {str(exc)}")
    
    # We use our standard InternalServerError class format
    error_response = {
        "success": False,
        "request_id": request_id_ctx.get(),
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred on the server",
            "details": {"type": exc.__class__.__name__} if request.app.debug else {},
        },
    }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response,
    )

def register_exception_handlers(app: FastAPI):
    """Register all standard exception handlers to the FastAPI app."""
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    logger.info("Standard exception handlers registered")
