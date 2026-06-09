"""
InferiaLLM permission/role catalog.

Defines the canonical set of permissions and roles that InferiaLLM self-declares
to InferiaAuth via PUT /api/v1/services/:id/catalog. This module contains only
pure data — no HTTP client, no config, no app wiring. Those concerns live in
later boot-time tasks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    """A single declared permission entry."""

    key: str
    display_name: str
    description: str


@dataclass(frozen=True)
class Role:
    """A catalog role with an ordered tuple of permission keys it grants."""

    name: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    """Immutable snapshot of all permissions and roles declared by this service."""

    permissions: tuple[Permission, ...]
    roles: tuple[Role, ...]


# ---------------------------------------------------------------------------
# Permission definitions — key format: inferiallm:<resource>:<action>
# Colon-form matches InferiaAuth's catalog key convention.
# ---------------------------------------------------------------------------

_PERMISSIONS: tuple[Permission, ...] = (
    Permission(
        key="inferiallm:deployment:read",
        display_name="View deployments",
        description="View model deployments and their status",
    ),
    Permission(
        key="inferiallm:deployment:write",
        display_name="Manage deployments",
        description="Create, update, and scale model deployments",
    ),
    Permission(
        key="inferiallm:deployment:delete",
        display_name="Delete deployments",
        description="Tear down model deployments",
    ),
    Permission(
        key="inferiallm:provider:read",
        display_name="View providers",
        description="View compute and model providers",
    ),
    Permission(
        key="inferiallm:provider:write",
        display_name="Manage providers",
        description="Configure compute and model providers",
    ),
    Permission(
        key="inferiallm:user:read",
        display_name="View users",
        description="View users in the organization",
    ),
    Permission(
        key="inferiallm:user:write",
        display_name="Manage users",
        description="Invite, edit, and remove users",
    ),
    Permission(
        key="inferiallm:org:read",
        display_name="View organization",
        description="View organization settings",
    ),
    Permission(
        key="inferiallm:org:write",
        display_name="Manage organization",
        description="Edit organization settings",
    ),
    Permission(
        key="inferiallm:audit:read",
        display_name="View audit log",
        description="View the administrative audit log",
    ),
    Permission(
        key="inferiallm:apikey:read",
        display_name="View API keys",
        description="View API key metadata",
    ),
    Permission(
        key="inferiallm:apikey:write",
        display_name="Manage API keys",
        description="Create and revoke API keys",
    ),
    Permission(
        key="inferiallm:model:read",
        display_name="View models",
        description="View the model catalog",
    ),
    Permission(
        key="inferiallm:model:write",
        display_name="Manage models",
        description="Add and configure models",
    ),
)

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

_ROLES: tuple[Role, ...] = (
    # admin — full access to every declared permission
    Role(
        name="admin",
        permissions=tuple(p.key for p in _PERMISSIONS),
    ),
    # member — read-only access to core resources + API key read
    Role(
        name="member",
        permissions=(
            "inferiallm:deployment:read",
            "inferiallm:provider:read",
            "inferiallm:user:read",
            "inferiallm:org:read",
            "inferiallm:model:read",
            "inferiallm:apikey:read",
        ),
    ),
    # viewer — minimal read-only surface
    Role(
        name="viewer",
        permissions=(
            "inferiallm:deployment:read",
            "inferiallm:provider:read",
            "inferiallm:model:read",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Module-level catalog constant
# ---------------------------------------------------------------------------

CATALOG: Catalog = Catalog(permissions=_PERMISSIONS, roles=_ROLES)

# ---------------------------------------------------------------------------
# Module-load integrity check — fail fast on any typo in role permission keys
# ---------------------------------------------------------------------------

_declared_keys: frozenset[str] = frozenset(p.key for p in CATALOG.permissions)
for _role in CATALOG.roles:
    for _key in _role.permissions:
        if _key not in _declared_keys:
            raise ValueError(
                f"Catalog integrity error: role '{_role.name}' references "
                f"undeclared permission key '{_key}'. "
                "Add it to _PERMISSIONS or fix the typo."
            )
del _declared_keys, _role, _key  # clean up module namespace


# ---------------------------------------------------------------------------
# Projection to InferiaAuth declare-request body
# ---------------------------------------------------------------------------


def to_declare_request(catalog: Catalog) -> dict:
    """
    Project a Catalog into the JSON body expected by InferiaAuth's
    PUT /api/v1/services/:id/catalog endpoint.

    Returns a plain dict ready for ``json.dumps`` or an httpx/requests call:

        {
            "permissions": [
                {"key": "...", "display_name": "...", "description": "..."},
                ...
            ],
            "roles": [
                {"name": "...", "permissions": ["...", ...]},
                ...
            ],
        }
    """
    return {
        "permissions": [
            {
                "key": p.key,
                "display_name": p.display_name,
                "description": p.description,
            }
            for p in catalog.permissions
        ],
        "roles": [
            {
                "name": r.name,
                "permissions": list(r.permissions),
            }
            for r in catalog.roles
        ],
    }
