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
