"""Route-registration guard for the AWS discovery proxy endpoints.

The gateway router has prefix /api/v1, so the registered paths are
/api/v1/admin/aws/regions and /api/v1/admin/aws/instance-types.
"""


def test_discovery_routes_registered():
    from api_gateway.app import app
    paths = {r.path for r in app.routes}
    # Gateway router prefix is /api/v1; full registered paths include it.
    assert "/api/v1/admin/aws/regions" in paths, (
        f"missing /api/v1/admin/aws/regions in {[p for p in paths if 'admin/aws' in p]}"
    )
    assert "/api/v1/admin/aws/instance-types" in paths, (
        f"missing /api/v1/admin/aws/instance-types in {[p for p in paths if 'admin/aws' in p]}"
    )
