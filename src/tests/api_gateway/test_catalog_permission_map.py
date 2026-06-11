"""Tests for the catalog → local permission bridge (rbac/permissions.py).

InferiaAuth issues `inferiallm:<resource>:<action>` catalog keys in the token's
permissions claim; the dashboard and backend route guards check the local
PermissionEnum vocabulary. CATALOG_PERMISSION_MAP / expand_catalog_permissions
bridge the two. These tests pin:
  - every declared catalog key (except provider, which has no local surface)
    has a mapping;
  - every mapped value is a valid PermissionEnum member;
  - expansion keeps originals and passes unknown keys through;
  - the map's keys stay in sync with the declared catalog.
"""
from __future__ import annotations

from api_gateway.rbac.catalog import CATALOG
from api_gateway.rbac.permissions import (
    CATALOG_PERMISSION_MAP,
    expand_catalog_permissions,
)
from api_gateway.schemas.auth import PermissionEnum

# Catalog resources with no local-vocabulary surface (no SPA gate / route guard
# checks a local permission for them) — they intentionally have no mapping.
UNMAPPED_CATALOG_PREFIXES = ("inferiallm:provider:",)


def test_every_mapped_value_is_a_valid_local_permission():
    valid = {p.value for p in PermissionEnum}
    for catalog_key, locals_ in CATALOG_PERMISSION_MAP.items():
        for perm in locals_:
            assert perm in valid, f"{catalog_key} maps to unknown local perm {perm!r}"


def test_every_declared_catalog_key_is_mapped_or_known_unmapped():
    declared = {p.key for p in CATALOG.permissions}
    for key in declared:
        if key.startswith(UNMAPPED_CATALOG_PREFIXES):
            assert key not in CATALOG_PERMISSION_MAP
            continue
        assert key in CATALOG_PERMISSION_MAP, f"declared catalog key {key} has no local mapping"


def test_map_has_no_stale_keys():
    declared = {p.key for p in CATALOG.permissions}
    for key in CATALOG_PERMISSION_MAP:
        assert key in declared, f"mapping references undeclared catalog key {key}"


def test_org_read_expands_to_organization_view():
    out = expand_catalog_permissions(["inferiallm:org:read"])
    assert "organization:view" in out
    assert "inferiallm:org:read" in out  # original kept


def test_full_admin_catalog_covers_every_spa_gate():
    """The SPA gates on these local permissions; a full catalog admin must
    satisfy all of them."""
    admin_perms = next(r.permissions for r in CATALOG.roles if r.name == "admin")
    out = set(expand_catalog_permissions(admin_perms))
    spa_gates = {
        "organization:view",
        "organization:update",
        "deployment:list",
        "deployment:create",
        "deployment:update",
        "deployment:delete",
        "model:list",
        "model:add",
        "model:delete",
        "api_key:list",
        "api_key:create",
        "api_key:revoke",
        "member:list",
        "member:invite",
        "member:delete",
        "role:list",
        "role:create",
        "role:update",
        "role:delete",
        "audit_log:list",
    }
    missing = spa_gates - out
    assert not missing, f"admin catalog does not grant SPA-gated perms: {missing}"


def test_unknown_keys_pass_through_untouched():
    out = expand_catalog_permissions(["something:else", "inferiallm:provider:read"])
    assert "something:else" in out
    assert "inferiallm:provider:read" in out
    assert len(out) == 2


def test_empty_and_whitespace_inputs():
    assert expand_catalog_permissions([]) == []
    assert expand_catalog_permissions(None) == []
    assert expand_catalog_permissions(["  ", ""]) == []


def test_local_perms_pass_through_idempotently():
    out = expand_catalog_permissions(["organization:view", "inferiallm:org:read"])
    assert out.count("organization:view") == 1
