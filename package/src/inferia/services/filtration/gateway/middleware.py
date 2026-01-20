"""
API Gateway middleware for request processing and validation.
"""

import uuid
import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to generate or validate X-Request-ID header."""
    
    async def dispatch(self, request: Request, call_next):
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Store in request state
        request.state.request_id = request_id
        
        # Process request
        response = await call_next(request)
        
        # Add to response headers
        response.headers["X-Request-ID"] = request_id
        
        return response


class StandardHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to extract and validate standard headers."""
    
    async def dispatch(self, request: Request, call_next):
        # Extract standard headers
        user_id = request.headers.get("X-User-ID")
        trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
        client_version = request.headers.get("X-Client-Version")
        
        # Store in request state
        request.state.user_id = user_id
        request.state.trace_id = trace_id
        request.state.client_version = client_version
        
        # Process request
        response = await call_next(request)
        
        # Add trace ID to response
        response.headers["X-Trace-ID"] = trace_id
        
        return response


class ProcessingTimeMiddleware(BaseHTTPMiddleware):
    """Middleware to track request processing time."""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate processing time
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        request.state.processing_time_ms = processing_time
        
        # Add to response headers
        response.headers["X-Processing-Time-MS"] = str(round(processing_time, 2))
        
        return response


class CORSMiddleware:
    """Custom CORS configuration (or use FastAPI's built-in CORSMiddleware)."""
    pass  # Can be extended if needed
