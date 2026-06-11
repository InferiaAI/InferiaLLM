"""Shadow-organization provisioning for external (inferiaauth/oidc) modes.

In external modes the token's ``org_id`` is the IdP's org UUID, which has no
local ``organizations`` row — yet local features key on ``user_ctx.org_id``
(GET /management/organizations/me, audit-log FKs, API keys, deployments).
Mirror the shadow-user pattern: ensure a local org row exists for the IdP org
id, enriched best-effort with the org's real name fetched from the IdP
(``GET /api/v1/orgs/{id}``) using the caller's own bearer token.

Identity remains owned by the IdP — this row is a local anchor, not a
management surface (org create/update stays 409-gated in external modes).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from services.api_gateway.config import httpx_verify, settings
from services.api_gateway.db.models import Organization, UserOrganization

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 5.0


def _fallback_name(org_id: str) -> str:
    return f"Organization {org_id[:8]}"


async def fetch_idp_org_name(
    org_id: str,
    bearer_token: Optional[str],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    """Fetch the org's display name from the IdP with the caller's token.

    Best-effort: returns ``None`` on any failure (no token, network error,
    non-2xx, malformed body) and never raises. Tolerates both the flat
    ``{"name": ...}`` shape (InferiaAuth's org DTO) and a nested
    ``{"org": {"name": ...}}`` envelope.
    """
    if not org_id or not bearer_token or not settings.external_auth_url:
        return None

    url = f"{settings.external_auth_url.rstrip('/')}/api/v1/orgs/{org_id}"
    _owned: Optional[httpx.AsyncClient] = None
    if client is None:
        _owned = httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, verify=httpx_verify(settings)
        )
        client = _owned

    try:
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {bearer_token}"}
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "fetch_idp_org_name: %s fetching org %s", type(exc).__name__, org_id
        )
        return None
    finally:
        if _owned is not None:
            await _owned.aclose()

    if not resp.is_success:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    org = data.get("org")
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


async def ensure_external_org(
    db: AsyncSession,
    org_id: Optional[str],
    *,
    user_id: Optional[str] = None,
    bearer_token: Optional[str] = None,
) -> None:
    """Ensure a local org row (and membership) exists for the IdP org id.

    Best-effort and NEVER raises — token resolution must not fail because of
    a provisioning hiccup. The IdP name is fetched only when the org row is
    first created (one HTTP call per new org, not per request). The local
    ``organizations.name`` column is UNIQUE, so name collisions fall back to
    a name suffixed with the org id prefix.
    """
    if not org_id:
        return

    try:
        res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = res.scalars().first()

        if org is None:
            name = (
                await fetch_idp_org_name(org_id, bearer_token)
                or _fallback_name(org_id)
            )
            db.add(Organization(id=org_id, name=name, log_payloads=True))
            try:
                await db.commit()
                logger.info(
                    "Provisioned shadow organization %s (%r) for external identity",
                    org_id,
                    name,
                )
            except IntegrityError:
                # Concurrent request created it, or the name is taken locally.
                await db.rollback()
                res = await db.execute(
                    select(Organization).where(Organization.id == org_id)
                )
                org = res.scalars().first()
                if org is None:
                    db.add(
                        Organization(
                            id=org_id,
                            name=f"{name} ({org_id[:8]})",
                            log_payloads=True,
                        )
                    )
                    await db.commit()

        if user_id:
            res = await db.execute(
                select(UserOrganization).where(
                    UserOrganization.user_id == user_id,
                    UserOrganization.org_id == org_id,
                )
            )
            if res.scalars().first() is None:
                db.add(
                    UserOrganization(user_id=user_id, org_id=org_id, role="member")
                )
                await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.warning(
            "ensure_external_org: provisioning failed for org %s", org_id,
            exc_info=True,
        )
