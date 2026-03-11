"""
Middleware for validating internal API key from service-to-service requests.
"""

from inferia.common.middleware import create_internal_auth_middleware
from inferia.services.api_gateway.config import settings

# Create the middleware using the shared factory
# This middleware only checks routes starting with /internal
internal_api_key_middleware = create_internal_auth_middleware(
    internal_api_key=settings.internal_api_key,
    check_path_prefix="/internal/"
)
