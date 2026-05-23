import shlex

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    InvalidBootstrapInput,
    build_user_data,
)


VALID = dict(
    bootstrap_token="tok-abc-123",
    control_plane_url="https://cp.example.com",
    node_name="i-abc",
    pool_id="00000000-0000-0000-0000-000000000001",
    image="ghcr.io/example/inferia-worker",
    image_tag="v1.0.0",
)


def test_renders_bash_script_with_all_pieces():
    script = build_user_data(**VALID)
    assert script.startswith("#!/bin/bash")
    assert "set -euo pipefail" in script
    assert "command -v docker" in script
    assert "nvidia-ctk" in script
    assert "docker run" in script
    assert "--gpus=all" in script
    assert "BOOTSTRAP_TOKEN=" in script
    assert "ghcr.io/example/inferia-worker:v1.0.0" in script


def test_size_under_16kb_with_long_inputs():
    script = build_user_data(
        bootstrap_token="x" * 128,
        control_plane_url="https://" + ("a" * 200) + ".example.com",
        node_name="i-" + "a" * 60,
        pool_id="00000000-0000-0000-0000-000000000001",
        image="ghcr.io/" + ("o" * 60) + "/inferia-worker",
        image_tag="v" + "9" * 30,
    )
    assert len(script.encode("utf-8")) <= 16 * 1024


@pytest.mark.parametrize(
    "field, malicious",
    [
        ("node_name", "i-abc; rm -rf /"),
        ("node_name", "i-abc' && curl evil"),
        ("node_name", "i-abc`whoami`"),
        ("node_name", "i-abc$(id)"),
        ("pool_id", "pool\nrm -rf /"),
        ("bootstrap_token", 'tok" && wget bad'),
        ("control_plane_url", "https://evil.com; rm -rf /"),
    ],
)
def test_shell_injection_resistance(field, malicious):
    args = dict(VALID, **{field: malicious})
    script = build_user_data(**args)
    # Use shlex.quote as the canonical check — handles all quoting styles
    assert shlex.quote(malicious) in script


def test_null_byte_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, node_name="i-abc\x00"))


def test_oversized_field_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, node_name="x" * 2000))


# ---------------------------------------------------------------------------
# Additional edge-case coverage to reach >95% branch/line coverage
# ---------------------------------------------------------------------------


def test_oversized_bootstrap_token_rejected():
    with pytest.raises(InvalidBootstrapInput, match="bootstrap_token"):
        build_user_data(**dict(VALID, bootstrap_token="t" * 2000))


def test_oversized_control_plane_url_rejected():
    with pytest.raises(InvalidBootstrapInput, match="control_plane_url"):
        build_user_data(**dict(VALID, control_plane_url="https://" + "x" * 2000))


def test_oversized_pool_id_rejected():
    with pytest.raises(InvalidBootstrapInput, match="pool_id"):
        build_user_data(**dict(VALID, pool_id="p" * 2000))


def test_oversized_image_rejected():
    with pytest.raises(InvalidBootstrapInput, match="^image "):
        build_user_data(**dict(VALID, image="ghcr.io/" + "x" * 2000))


def test_oversized_image_tag_rejected():
    with pytest.raises(InvalidBootstrapInput, match="image_tag"):
        build_user_data(**dict(VALID, image_tag="v" + "9" * 2000))


def test_null_byte_in_bootstrap_token_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, bootstrap_token="tok\x00abc"))


def test_null_byte_in_control_plane_url_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, control_plane_url="https://cp\x00.example.com"))


def test_null_byte_in_pool_id_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, pool_id="pool\x00id"))


def test_null_byte_in_image_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, image="ghcr.io/\x00/inferia-worker"))


def test_null_byte_in_image_tag_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, image_tag="v1.\x000"))


def test_image_full_combines_image_and_tag():
    script = build_user_data(**VALID)
    expected_image_full = shlex.quote(f"{VALID['image']}:{VALID['image_tag']}")
    assert expected_image_full in script


def test_script_has_restart_policy():
    script = build_user_data(**VALID)
    assert "--restart=always" in script


def test_script_mounts_docker_sock():
    script = build_user_data(**VALID)
    assert "/var/run/docker.sock" in script


def test_script_logs_to_file():
    script = build_user_data(**VALID)
    assert "inferia-bootstrap.log" in script


def test_pool_id_injection_resistance():
    malicious_pool = "00000000-0000-0000-0000-000000000001; DROP TABLE workers;"
    script = build_user_data(**dict(VALID, pool_id=malicious_pool))
    assert shlex.quote(malicious_pool) in script


def test_empty_strings_are_valid():
    """Empty string should pass validation (it's ≤1024 chars and has no NUL)."""
    # All fields empty is unusual but not rejected by _validate itself.
    # build_user_data should produce a script (even if nonsensical).
    script = build_user_data(
        bootstrap_token="",
        control_plane_url="",
        node_name="",
        pool_id="",
        image="",
        image_tag="",
    )
    assert script.startswith("#!/bin/bash")


def test_exactly_1024_chars_accepted():
    """A field of exactly 1024 chars is at the boundary and must be accepted."""
    script = build_user_data(**dict(VALID, node_name="x" * 1024))
    assert shlex.quote("x" * 1024) in script


def test_exactly_1025_chars_rejected():
    """A field of exactly 1025 chars must be rejected."""
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, node_name="x" * 1025))
