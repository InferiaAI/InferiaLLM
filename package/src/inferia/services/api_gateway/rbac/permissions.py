from __future__ import annotations

from typing import Iterable, List, Tuple

from inferia.services.api_gateway.schemas.auth import PermissionEnum

# Deprecated permission aliases retained for data migration/backward compatibility.
DEPRECATED_PERMISSION_MAP = {
    "user:list": PermissionEnum.MEMBER_LIST.value,
    "user:view": PermissionEnum.MEMBER_LIST.value,
    "user:update": PermissionEnum.ROLE_UPDATE.value,
    "user:delete": PermissionEnum.MEMBER_DELETE.value,
}

DEPRECATED_SUPER_PERMISSION = "admin:all"


def canonical_permissions() -> List[str]:
    return sorted({p.value for p in PermissionEnum})


# ---------------------------------------------------------------------------
# Catalog → local permission bridge (inferiaauth / oidc modes)
# ---------------------------------------------------------------------------
# InferiaAuth issues org-scoped permissions in the CATALOG vocabulary
# (`inferiallm:<resource>:<action>`, declared in rbac/catalog.py), while local
# route guards and the dashboard check the LOCAL PermissionEnum vocabulary
# (`organization:view`, `deployment:list`, ...). This map is that bridge: each
# catalog key fans out to the local permissions it implies. Keys without a
# local equivalent (e.g. provider) simply pass through unexpanded.
CATALOG_PERMISSION_MAP: dict = {
    "inferiallm:deployment:read": (PermissionEnum.DEPLOYMENT_LIST.value,),
    "inferiallm:deployment:write": (
        PermissionEnum.DEPLOYMENT_CREATE.value,
        PermissionEnum.DEPLOYMENT_UPDATE.value,
    ),
    "inferiallm:deployment:delete": (PermissionEnum.DEPLOYMENT_DELETE.value,),
    "inferiallm:model:read": (
        PermissionEnum.MODEL_LIST.value,
        PermissionEnum.MODEL_ACCESS.value,
    ),
    "inferiallm:model:write": (
        PermissionEnum.MODEL_ADD.value,
        PermissionEnum.MODEL_DELETE.value,
    ),
    "inferiallm:apikey:read": (PermissionEnum.API_KEY_LIST.value,),
    "inferiallm:apikey:write": (
        PermissionEnum.API_KEY_CREATE.value,
        PermissionEnum.API_KEY_REVOKE.value,
    ),
    "inferiallm:user:read": (
        PermissionEnum.MEMBER_LIST.value,
        PermissionEnum.ROLE_LIST.value,
    ),
    "inferiallm:user:write": (
        PermissionEnum.MEMBER_INVITE.value,
        PermissionEnum.MEMBER_DELETE.value,
        PermissionEnum.ROLE_CREATE.value,
        PermissionEnum.ROLE_UPDATE.value,
        PermissionEnum.ROLE_DELETE.value,
    ),
    "inferiallm:org:read": (PermissionEnum.ORG_VIEW.value,),
    "inferiallm:org:write": (PermissionEnum.ORG_UPDATE.value,),
    "inferiallm:audit:read": (PermissionEnum.AUDIT_LOG_LIST.value,),
}


def expand_catalog_permissions(permissions: Iterable[str]) -> List[str]:
    """Union the input with the local-vocabulary equivalents of catalog keys.

    The original keys are KEPT (so consumers may still check the catalog form);
    unknown/unmapped keys pass through untouched. Used when building the
    UserContext from an external (inferiaauth/oidc) token so that both the
    backend route guards and the dashboard — which check the local
    PermissionEnum vocabulary — recognise catalog-granted access.
    """
    expanded = set()
    for permission in permissions or []:
        perm = (permission or "").strip()
        if not perm:
            continue
        expanded.add(perm)
        expanded.update(CATALOG_PERMISSION_MAP.get(perm, ()))
    return sorted(expanded)


def normalize_permissions(permissions: Iterable[str]) -> Tuple[List[str], List[str], List[str]]:
    """
    Normalize permissions to the canonical set.

    Returns: (normalized_permissions, mapped_deprecated, unknown_permissions)
    """
    canonical = set(canonical_permissions())
    normalized = set()
    mapped_deprecated = []
    unknown_permissions = []

    for permission in permissions or []:
        perm = (permission or "").strip()
        if not perm:
            continue

        if perm in canonical:
            normalized.add(perm)
            continue

        if perm == DEPRECATED_SUPER_PERMISSION:
            normalized.update(canonical)
            mapped_deprecated.append(perm)
            continue

        mapped = DEPRECATED_PERMISSION_MAP.get(perm)
        if mapped:
            normalized.add(mapped)
            mapped_deprecated.append(perm)
            continue

        unknown_permissions.append(perm)

    return sorted(normalized), sorted(set(mapped_deprecated)), sorted(set(unknown_permissions))
