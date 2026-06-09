from inferia.services.api_gateway.rbac.catalog import CATALOG, to_declare_request


def test_every_role_permission_is_declared():
    keys = {p.key for p in CATALOG.permissions}
    for role in CATALOG.roles:
        for k in role.permissions:
            assert k in keys, f"role {role.name} references undeclared permission {k}"


def test_admin_has_all_permissions():
    admin = next(r for r in CATALOG.roles if r.name == "admin")
    assert set(admin.permissions) == {p.key for p in CATALOG.permissions}


def test_permission_keys_are_inferiallm_namespaced():
    assert CATALOG.permissions, "catalog must declare permissions"
    assert all(p.key.startswith("inferiallm:") for p in CATALOG.permissions)


def test_role_names():
    assert {r.name for r in CATALOG.roles} == {"admin", "member", "viewer"}


def test_to_declare_request_shape():
    body = to_declare_request(CATALOG)
    assert set(body.keys()) == {"roles", "permissions"}
    for p in body["permissions"]:
        assert {"key", "display_name", "description"} <= set(p.keys())
        assert all(isinstance(p[f], str) and p[f] for f in ("key", "display_name", "description"))
    for r in body["roles"]:
        assert {"name", "permissions"} <= set(r.keys())
        assert isinstance(r["permissions"], list)
