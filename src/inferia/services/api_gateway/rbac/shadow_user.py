"""
Shadow-user provisioning for external auth (inferia-auth / oidc).

When a user authenticates through an external IdP, we need a corresponding
row in InferiaLLM's local DB so that org membership, permissions, API keys,
and audit logs can reference a real user record.

The shadow user is created on first login and reused on subsequent ones.

Design notes:
- On creation, default_org_id is set to None and NO UserOrganization row is
  created.  The correct membership row is provisioned immediately afterwards
  by ensure_external_org() in the middleware resolvers using the IdP's org_id
  claim.  Attaching to a local "Default Organization" would pollute that org
  with external users and cause /auth/organizations to report the wrong org.
- The empty-membership fallback attach that existed previously has been
  removed for the same reason.
- Callers in middleware ignore the returned org_id / roles values (they take
  those from the JWT claims).  The return signature (user, org_id_or_none,
  roles_list) is kept for API stability.
"""

import logging
import uuid
from typing import Tuple, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from inferia.services.api_gateway.db.models import (
    User as DBUser,
    UserOrganization,
)

logger = logging.getLogger(__name__)

# Placeholder hash — shadow users never authenticate locally.
_EXTERNAL_PASSWORD_PLACEHOLDER = "!external-auth-managed!"


async def get_or_create_shadow_user(
    db: AsyncSession,
    *,
    email: str,
    external_id: str,
) -> Tuple[DBUser, Optional[str], List[str]]:
    """Return (user, org_id_or_none, roles) for the given external identity.

    If no local user exists with this email a shadow record is created with
    ``default_org_id=None`` and no UserOrganization row — the IdP's org is
    provisioned by ``ensure_external_org`` immediately after this call.

    On subsequent logins the existing user row is returned.  The returned
    org_id / roles are derived from any existing membership rows; middleware
    callers treat these as informational only (authoritative data comes from
    the JWT claims).
    """
    normalized_email = email.strip().lower()

    result = await db.execute(
        select(DBUser).where(func.lower(DBUser.email) == normalized_email)
    )
    user = result.scalars().first()

    if user is None:
        # Create shadow user without attaching it to any local org.
        # ensure_external_org() will provision the correct IdP-org membership.
        user = DBUser(
            id=str(uuid.uuid4()),
            email=normalized_email,
            password_hash=_EXTERNAL_PASSWORD_PLACEHOLDER,
            default_org_id=None,
        )
        db.add(user)
        await db.flush()
        await db.commit()
        await db.refresh(user)
        logger.info("Created shadow user for external identity: %s", normalized_email)

    # Resolve org membership (informational; middleware uses JWT claims).
    membership_stmt = (
        select(UserOrganization)
        .where(UserOrganization.user_id == user.id)
        .order_by(UserOrganization.created_at.asc())
    )
    membership_res = await db.execute(membership_stmt)
    memberships = membership_res.scalars().all()

    if not memberships:
        # No membership yet — ensure_external_org will add it shortly.
        return user, None, []

    target_org_id = user.default_org_id
    target_role = "member"
    found = False

    for m in memberships:
        if target_org_id and m.org_id == target_org_id:
            target_role = m.role
            found = True
            break

    if not found and memberships:
        target_org_id = memberships[0].org_id
        target_role = memberships[0].role

    return user, target_org_id, [target_role]
