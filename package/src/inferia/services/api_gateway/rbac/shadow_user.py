"""
Shadow-user provisioning for external auth (inferia-auth).

When a user authenticates through inferia-auth, we need a corresponding
row in InferiaLLM's local DB so that org membership, permissions, API keys,
and audit logs can reference a real user record.

The shadow user is created on first login and reused on subsequent ones.
"""

import logging
import uuid
from typing import Tuple, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from inferia.services.api_gateway.db.models import (
    User as DBUser,
    Organization,
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
) -> Tuple[DBUser, str, List[str]]:
    """
    Return (user, org_id, roles) for the given external identity.

    If no local user exists with this email a shadow record is created,
    added to the default organization as a ``member``.
    """
    normalized_email = email.strip().lower()

    result = await db.execute(
        select(DBUser).where(func.lower(DBUser.email) == normalized_email)
    )
    user = result.scalars().first()

    if user is None:
        # Create shadow user
        org_stmt = select(Organization).limit(1)
        org_res = await db.execute(org_stmt)
        default_org = org_res.scalars().first()

        user = DBUser(
            id=str(uuid.uuid4()),
            email=normalized_email,
            password_hash=_EXTERNAL_PASSWORD_PLACEHOLDER,
            default_org_id=default_org.id if default_org else None,
        )
        db.add(user)
        await db.flush()

        if default_org:
            uo = UserOrganization(
                user_id=user.id, org_id=default_org.id, role="member"
            )
            db.add(uo)

        await db.commit()
        await db.refresh(user)
        logger.info("Created shadow user for external identity: %s", normalized_email)

    # Resolve org membership
    membership_stmt = (
        select(UserOrganization)
        .where(UserOrganization.user_id == user.id)
        .order_by(UserOrganization.created_at.asc())
    )
    membership_res = await db.execute(membership_stmt)
    memberships = membership_res.scalars().all()

    if not memberships:
        # Assign to default org as fallback
        org_stmt = select(Organization).limit(1)
        org_res = await db.execute(org_stmt)
        default_org = org_res.scalars().first()
        if default_org:
            uo = UserOrganization(
                user_id=user.id, org_id=default_org.id, role="member"
            )
            db.add(uo)
            await db.commit()
            memberships = [uo]

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
