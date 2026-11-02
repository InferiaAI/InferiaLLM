"""Open-source ``token_verifier`` default (interfaces.md §6, §7)."""

from typing import Any

from api_gateway.models import UserContext


class DefaultTokenVerifier:
    """Dispatches on ``AUTH_PROVIDER`` exactly as before the extension point:
    local tokens first (superadmin recovery), then the external resolver in
    ``oidc`` / ``inferiaauth`` mode. See ``resolve_token_to_user_context``.

    Uses the caller's session when given, else opens one.
    """

    async def verify(self, token: str, *, db: Any = None) -> UserContext:
        from api_gateway.rbac import middleware

        if db is not None:
            return await middleware.resolve_token_to_user_context(db, token)
        async with middleware.AsyncSessionLocal() as session:
            return await middleware.resolve_token_to_user_context(session, token)
