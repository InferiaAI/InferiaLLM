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
    inference_token="inf-tok-xyz",
    # T12: build_user_data now branches on instance_class. Default the
    # existing happy-path fixtures to a GPU tier so all the pre-existing
    # assertions (nvidia-ctk, --gpus, etc.) continue to hold.
    instance_class="normal_gpu",
    gpu_count=1,
)


def test_renders_bash_script_with_all_pieces():
    script = build_user_data(**VALID)
    assert script.startswith("#!/bin/bash")
    assert "set -euo pipefail" in script
    assert "command -v docker" in script
    assert "nvidia-ctk" in script
    assert "docker run" in script
    # T12 standardized on the space-separated --gpus all form (matches
    # the form passed to docker run by the new branching template).
    assert "--gpus all" in script
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
        inference_token="inf-tok-" + "y" * 100,
        instance_class="normal_gpu",
        gpu_count=1,
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
        inference_token="",
        instance_class="normal_gpu",
        gpu_count=1,
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


# ---------------------------------------------------------------------------
# T12: instance_class branching tests (verbatim from the plan, adapted to
# the existing builder's kwarg names — we use worker_image as the
# single-string image alias so the plan-shape kwargs work unchanged).
# ---------------------------------------------------------------------------


def _common_kwargs(**over):
    base = dict(
        bootstrap_token="bt",
        pool_id="p",
        node_name="n",
        control_plane_url="https://cp",
        inference_token="it",
        worker_image="inferia-worker:latest",
        instance_class="normal_gpu",
        gpu_count=1,
    )
    base.update(over)
    return base


def test_normal_gpu_userdata_installs_nvidia_container_runtime():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "nvidia-container-runtime" in ud or "nvidia-container-toolkit" in ud


def test_normal_gpu_userdata_sets_allocatable_gpu_override_to_count():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "ALLOCATABLE_GPU_OVERRIDE=1" in ud


def test_heavy_gpu_userdata_sets_allocatable_gpu_override_to_count():
    ud = build_user_data(**_common_kwargs(instance_class="heavy_gpu", gpu_count=8))
    assert "ALLOCATABLE_GPU_OVERRIDE=8" in ud


def test_cpu_userdata_skips_nvidia_container_runtime():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "nvidia-container-runtime" not in ud
    assert "nvidia-container-toolkit" not in ud


def test_cpu_userdata_sets_allocatable_gpu_override_zero():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "ALLOCATABLE_GPU_OVERRIDE=0" in ud


def test_cpu_userdata_does_not_pass_gpus_all_to_docker_run():
    ud = build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=0))
    assert "--gpus all" not in ud


def test_normal_gpu_userdata_passes_gpus_all_to_docker_run():
    ud = build_user_data(**_common_kwargs(instance_class="normal_gpu", gpu_count=1))
    assert "--gpus all" in ud


def test_unknown_instance_class_raises():
    with pytest.raises(ValueError):
        build_user_data(**_common_kwargs(instance_class="quantum_gpu", gpu_count=99))


# --- T12 code review I-2 + I-4 follow-ups: gpu_count + worker_image guards


def test_negative_gpu_count_raises():
    with pytest.raises(ValueError, match="gpu_count must be >= 0"):
        build_user_data(**_common_kwargs(gpu_count=-1))


def test_cpu_with_nonzero_gpu_count_raises():
    """instance_class='cpu' is incompatible with gpu_count > 0; catch
    at boot-script generation time rather than worker register time."""
    with pytest.raises(ValueError, match="cpu.*gpu_count=0"):
        build_user_data(**_common_kwargs(instance_class="cpu", gpu_count=2))


def test_bool_gpu_count_raises():
    """bool is a subclass of int but passing True/False is almost
    certainly a wiring bug."""
    with pytest.raises(ValueError, match="non-bool int"):
        build_user_data(**_common_kwargs(gpu_count=True))


def test_worker_image_mutually_exclusive_with_image_and_image_tag():
    with pytest.raises(ValueError, match="mutually exclusive"):
        # _common_kwargs uses worker_image; explicitly also pass image
        # to trigger the new guard.
        build_user_data(**_common_kwargs(worker_image="repo/x:tag",
                                            image="repo/x", image_tag="tag"))
