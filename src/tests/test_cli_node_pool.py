"""
Unit tests for U4 (cmd_pool_aws_config) and U5 (cmd_pool_show) CLI commands.

Endpoint conventions (real orchestration API):
  GET  /deployment/pool/{pool_id}       → {pool_id, pool_name, provider,
                                           lifecycle_state, …}  (NO metadata field)
  PATCH /deployment/updatepool/{pool_id} → {pool_id, provider, metadata, status}
    metadata=null  → no-op read; returns current metadata row
    metadata={...} → merge-write; returns merged metadata
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cli.node import cmd_pool_aws_config, cmd_pool_show


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_args(**kw):
    """Build a minimal argparse.Namespace-like object for testing."""

    class A:
        pool_id = kw.get("pool_id", "00000000-0000-0000-0000-000000000001")
        subnet = kw.get("subnet")
        security_group = kw.get("security_group", [])
        ami = kw.get("ami")
        iam_profile = kw.get("iam_profile")
        root_gb = kw.get("root_gb")
        image_tag = kw.get("image_tag")
        orchestration_url = kw.get("orchestration_url", "http://localhost:8080")
        internal_api_key = kw.get("internal_api_key", "test-key")
        org_id = kw.get("org_id")

    return A()


# ---------------------------------------------------------------------------
# cmd_pool_aws_config — validation errors (no HTTP calls)
# ---------------------------------------------------------------------------


def test_pool_aws_config_missing_subnet():
    """--subnet is required; absence must exit with code 2."""
    args = _fake_args(security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_subnet():
    """Bogus subnet_id must be rejected client-side (exit 2)."""
    args = _fake_args(subnet="bogus", security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_missing_security_group():
    """At least one --security-group is required (exit 2)."""
    args = _fake_args(subnet="subnet-abc12345", security_group=[])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_sg():
    """Bogus security-group ID must be rejected (exit 2)."""
    args = _fake_args(subnet="subnet-abc12345", security_group=["bogus"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_ami():
    """Bogus AMI ID must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        ami="not-an-ami",
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_iam_profile():
    """Bogus IAM instance-profile ARN must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        iam_profile="not-an-arn",
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_root_gb_too_small():
    """root_gb < 10 must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        root_gb=9,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_invalid_root_gb_too_large():
    """root_gb > 16384 must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        root_gb=16385,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_image_tag_whitespace():
    """image_tag with whitespace must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        image_tag="tag with space",
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


def test_pool_aws_config_image_tag_only_whitespace():
    """image_tag of only whitespace must be rejected (exit 2)."""
    args = _fake_args(
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
        image_tag="   ",
    )
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# cmd_pool_aws_config — HTTP-level errors
# ---------------------------------------------------------------------------


def test_pool_aws_config_pool_not_found(monkeypatch):
    """GET 404 → exit 1 (pool not found)."""

    def fake_http(method, url, *, headers, body=None):
        return 404, b""

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 1


def test_pool_aws_config_pool_get_server_error(monkeypatch):
    """GET 500 → exit 1."""

    def fake_http(method, url, *, headers, body=None):
        return 500, b"internal error"

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 1


def test_pool_aws_config_non_aws_pool_rejected(monkeypatch):
    """Pool with provider != 'aws' must be rejected with exit 1."""

    def fake_http(method, url, *, headers, body=None):
        return 200, json.dumps(
            {"pool_id": "pool-1", "provider": "nosana", "pool_name": "test"}
        ).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 1


def test_pool_aws_config_patch_failure(monkeypatch):
    """PATCH returning non-2xx → exit 1."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps(
                {"pool_id": "pool-1", "provider": "aws", "pool_name": "test"}
            ).encode()
        return 422, b'{"detail": "validation error"}'

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    with pytest.raises(SystemExit) as exc:
        cmd_pool_aws_config(args)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# cmd_pool_aws_config — happy path
# ---------------------------------------------------------------------------


def test_pool_aws_config_happy(monkeypatch, capsys):
    """Happy path: verifies GET then PATCH are called with correct payloads."""
    calls = []

    def fake_http(method, url, *, headers, body=None):
        calls.append((method, url, json.loads(body) if body else None))
        if method == "GET":
            return 200, json.dumps(
                {
                    "pool_id": "pool-1",
                    "provider": "aws",
                    "pool_name": "us-east-1",
                }
            ).encode()
        # PATCH
        return 200, json.dumps(
            {
                "pool_id": "pool-1",
                "provider": "aws",
                "metadata": {
                    "subnet_id": "subnet-abc12345",
                    "security_group_ids": ["sg-abc12345"],
                },
                "status": "UPDATED",
            }
        ).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(
        pool_id="pool-1",
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
    )
    cmd_pool_aws_config(args)

    assert len(calls) == 2
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"

    patch_payload = calls[1][2]
    meta = patch_payload["metadata"]
    assert meta["subnet_id"] == "subnet-abc12345"
    assert meta["security_group_ids"] == ["sg-abc12345"]

    out = capsys.readouterr().out
    assert "updated pool" in out and "pool-1" in out


def test_pool_aws_config_all_optional_fields(monkeypatch, capsys):
    """All optional fields are included when provided."""
    calls = []

    def fake_http(method, url, *, headers, body=None):
        calls.append((method, url, json.loads(body) if body else None))
        if method == "GET":
            return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
        return 200, json.dumps({"pool_id": "p", "provider": "aws", "metadata": {}, "status": "UPDATED"}).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(
        subnet="subnet-abc12345678",
        security_group=["sg-abc12345", "sg-def67890"],
        ami="ami-0abcdef1234567890",
        iam_profile="arn:aws:iam::123456789012:instance-profile/MyProfile",
        root_gb=200,
        image_tag="v1.2.3",
    )
    cmd_pool_aws_config(args)

    meta = calls[1][2]["metadata"]
    assert meta["ami_id"] == "ami-0abcdef1234567890"
    assert meta["iam_instance_profile"] == "arn:aws:iam::123456789012:instance-profile/MyProfile"
    assert meta["root_volume_gb"] == 200
    assert meta["worker_image_tag"] == "v1.2.3"
    assert meta["security_group_ids"] == ["sg-abc12345", "sg-def67890"]


def test_pool_aws_config_root_gb_boundary_valid(monkeypatch):
    """root_gb at exact boundaries 10 and 16384 must be accepted."""
    for gb in (10, 16384):
        calls = []

        def fake_http(method, url, *, headers, body=None):
            calls.append(method)
            if method == "GET":
                return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
            return 200, json.dumps({"pool_id": "p", "provider": "aws", "metadata": {}, "status": "UPDATED"}).encode()

        with patch("cli.node._http", fake_http):
            args = _fake_args(
                subnet="subnet-abc12345",
                security_group=["sg-abc12345"],
                root_gb=gb,
            )
            cmd_pool_aws_config(args)  # should not raise


def test_pool_aws_config_optional_fields_omitted_when_none(monkeypatch):
    """Optional fields not provided must not appear in the PATCH metadata."""
    calls = []

    def fake_http(method, url, *, headers, body=None):
        calls.append((method, json.loads(body) if body else None))
        if method == "GET":
            return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
        return 200, json.dumps({"pool_id": "p", "provider": "aws", "metadata": {}, "status": "UPDATED"}).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    cmd_pool_aws_config(args)

    meta = calls[1][1]["metadata"]
    assert "ami_id" not in meta
    assert "iam_instance_profile" not in meta
    assert "root_volume_gb" not in meta
    assert "worker_image_tag" not in meta


def test_pool_aws_config_204_accepted(monkeypatch, capsys):
    """PATCH returning 204 (No Content) must also be treated as success."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
        return 204, b""

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(subnet="subnet-abc12345", security_group=["sg-abc12345"])
    cmd_pool_aws_config(args)  # must not raise
    assert "updated pool" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_pool_show — tests
# ---------------------------------------------------------------------------


def test_pool_show_aws(monkeypatch, capsys):
    """AWS pool with metadata should render the AWS configuration section."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps(
                {
                    "pool_id": "pool-1",
                    "provider": "aws",
                    "pool_name": "us-east-1",
                    "lifecycle_state": "running",
                }
            ).encode()
        # PATCH with metadata=null → no-op read
        return 200, json.dumps(
            {
                "pool_id": "pool-1",
                "provider": "aws",
                "metadata": {
                    "subnet_id": "subnet-abc12345",
                    "security_group_ids": ["sg-abc12345"],
                    "root_volume_gb": 100,
                },
                "status": "UPDATED",
            }
        ).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="pool-1")
    cmd_pool_show(args)

    out = capsys.readouterr().out
    assert "pool-1" in out
    assert "aws" in out
    assert "subnet-abc12345" in out
    assert "sg-abc12345" in out
    assert "AWS configuration" in out


def test_pool_show_nosana(monkeypatch, capsys):
    """Non-AWS pool metadata should be rendered as generic JSON block."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps(
                {
                    "pool_id": "pool-2",
                    "provider": "nosana",
                    "pool_name": "sol-mainnet",
                    "lifecycle_state": "running",
                }
            ).encode()
        return 200, json.dumps(
            {
                "pool_id": "pool-2",
                "provider": "nosana",
                "metadata": {"provider_pool_id": "0xabcd"},
                "status": "UPDATED",
            }
        ).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="pool-2")
    cmd_pool_show(args)

    out = capsys.readouterr().out
    assert "0xabcd" in out
    assert "Metadata" in out
    assert "AWS configuration" not in out


def test_pool_show_not_found(monkeypatch):
    """GET 404 → exit 1."""

    def fake_http(method, url, *, headers, body=None):
        return 404, b""

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="missing")
    with pytest.raises(SystemExit) as exc:
        cmd_pool_show(args)
    assert exc.value.code == 1


def test_pool_show_get_server_error(monkeypatch):
    """GET 500 → exit 1."""

    def fake_http(method, url, *, headers, body=None):
        return 500, b"oops"

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="p")
    with pytest.raises(SystemExit) as exc:
        cmd_pool_show(args)
    assert exc.value.code == 1


def test_pool_show_no_metadata(monkeypatch, capsys):
    """AWS pool with no metadata yet still renders AWS section with dashes."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps(
                {"pool_id": "pool-3", "provider": "aws", "pool_name": "test"}
            ).encode()
        return 200, json.dumps(
            {"pool_id": "pool-3", "provider": "aws", "metadata": None, "status": "UPDATED"}
        ).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="pool-3")
    cmd_pool_show(args)
    out = capsys.readouterr().out
    assert "AWS configuration" in out
    assert "subnet_id" in out


def test_pool_show_patch_fails_gracefully(monkeypatch, capsys):
    """If the metadata PATCH fails, show still prints basic pool info."""

    def fake_http(method, url, *, headers, body=None):
        if method == "GET":
            return 200, json.dumps(
                {"pool_id": "pool-4", "provider": "aws", "pool_name": "test"}
            ).encode()
        return 500, b"error"

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="pool-4")
    # Should not raise; just show what we have
    cmd_pool_show(args)
    out = capsys.readouterr().out
    assert "pool-4" in out
    assert "aws" in out


def test_pool_show_uses_correct_endpoints(monkeypatch, capsys):
    """Verify the exact URL paths used for GET and PATCH."""
    urls_seen = []

    def fake_http(method, url, *, headers, body=None):
        urls_seen.append((method, url))
        if method == "GET":
            return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
        return 200, json.dumps({"pool_id": "p", "provider": "aws", "metadata": {}, "status": "UPDATED"}).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(pool_id="abc-123")
    cmd_pool_show(args)

    assert any("/deployment/pool/abc-123" in u for _, u in urls_seen), \
        f"Expected GET /deployment/pool/abc-123 in {urls_seen}"
    assert any("/deployment/updatepool/abc-123" in u for _, u in urls_seen), \
        f"Expected PATCH /deployment/updatepool/abc-123 in {urls_seen}"


def test_pool_aws_config_uses_correct_endpoints(monkeypatch, capsys):
    """Verify the exact URL paths used for GET and PATCH in aws-config."""
    urls_seen = []

    def fake_http(method, url, *, headers, body=None):
        urls_seen.append((method, url))
        if method == "GET":
            return 200, json.dumps({"pool_id": "p", "provider": "aws"}).encode()
        return 200, json.dumps({"pool_id": "p", "provider": "aws", "metadata": {}, "status": "UPDATED"}).encode()

    monkeypatch.setattr("cli.node._http", fake_http)
    args = _fake_args(
        pool_id="abc-123",
        subnet="subnet-abc12345",
        security_group=["sg-abc12345"],
    )
    cmd_pool_aws_config(args)

    assert any("/deployment/pool/abc-123" in u for _, u in urls_seen), \
        f"Expected GET /deployment/pool/abc-123 in {urls_seen}"
    assert any("/deployment/updatepool/abc-123" in u for _, u in urls_seen), \
        f"Expected PATCH /deployment/updatepool/abc-123 in {urls_seen}"
