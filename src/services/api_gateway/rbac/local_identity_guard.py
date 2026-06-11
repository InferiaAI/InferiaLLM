from fastapi import HTTPException, status
from services.api_gateway.config import settings


def require_local_identity() -> None:
    """Block local org/user/team/role management when an external IdP owns identity.

    In oidc/inferiaauth modes these resources are managed by the IdP
    (InferiaAuth or the enterprise's own OIDC), so InferiaLLM returns 409.
    A no-op in local mode.
    """
    if settings.is_external_mode:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Organization, user, and team management is handled by your "
                "identity provider in this deployment mode."
            ),
        )
