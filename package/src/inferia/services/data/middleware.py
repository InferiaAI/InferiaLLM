"""
Middleware for validating internal API key from service-to-service requests.
"""

from inferia.common.middleware import create_internal_auth_middleware
from inferia.services.data.config import settings

# Create the middleware using the shared factory
# This middleware checks all routes except /health
internal_auth_middleware = create_internal_auth_middleware(
    internal_api_key=settings.internal_api_key,
    skip_paths=["/health"]
)
