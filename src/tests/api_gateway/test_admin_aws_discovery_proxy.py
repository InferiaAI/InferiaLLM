"""Route-registration guard for the AWS discovery proxy endpoints.

The gateway compute router has prefix /v1, so the registered paths are
/v1/admin/aws/regions and /v1/admin/aws/instance-types.
"""


def test_discovery_routes_registered():
    from api_gateway.app import app
    paths = {r.path for r in app.routes}
    # Gateway compute router prefix is /v1; full registered paths include it.
    assert "/v1/admin/aws/regions" in paths, (
        f"missing /v1/admin/aws/regions in {[p for p in paths if 'admin/aws' in p]}"
    )
    assert "/v1/admin/aws/instance-types" in paths, (
        f"missing /v1/admin/aws/instance-types in {[p for p in paths if 'admin/aws' in p]}"
    )
