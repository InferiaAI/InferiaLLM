"""
Regression tests for the Nosana vLLM CUDA forward-compat driver fix.

Root cause (confirmed live + locally reproduced): the vllm/vllm-openai and
vllm-omni images bake a CUDA forward-compat libcuda into their ld.so cache ahead
of the standard lib dirs. On Nosana GPU nodes (whose NVIDIA driver is newer than
the baked compat lib) the loader binds the mismatched compat libcuda and vLLM
EngineCore CUDA init dies with "Error 803: system has unsupported display driver
/ cuda driver combination" before any weight is fetched -- the container exits,
Nosana reports the job COMPLETED, and the inference endpoint never serves (503).

The fix sets LD_LIBRARY_PATH so the host driver's libcuda (injected by the
container runtime into a standard dir) is found before the ld.so cache. The
injection dir varies by host/runtime, so the value lists every common location.
NVIDIA_DISABLE_CUDA_COMPAT=1 alone is INSUFFICIENT (verified live: the broken
container set it and still hit Error 803).

These tests pin the env (and the vLLM --host bind) so the fix cannot silently
regress.
"""

from unittest.mock import patch

import pytest

# Patch settings before importing job_builder so it doesn't fail on missing config.
_mock_settings = type("Settings", (), {"internal_api_key": "test-key"})()

with patch("orchestration.config.settings", _mock_settings):
    from providers.nosana.job_builder import (
        CUDA_DRIVER_LD_LIBRARY_PATH,
        create_vllm_job,
        create_vllm_omni_job,
        create_ollama_job,
        build_job_definition,
    )

MODEL = "Qwen/Qwen3-0.6B"


def _op_args(job_or_def):
    """Return the container op's args dict for either a {op,meta} or full def."""
    if "op" in job_or_def:
        return job_or_def["op"]["args"]
    return job_or_def["ops"][0]["args"]


# ---------------------------------------------------------------------------
# The shared constant
# ---------------------------------------------------------------------------
class TestCudaDriverLdLibraryPathConstant:
    def test_multiarch_dir_is_first(self):
        # AWS DLAMI / standard nvidia-container-toolkit on Ubuntu injects here;
        # it must be searched first so those hosts keep working.
        assert CUDA_DRIVER_LD_LIBRARY_PATH.split(":")[0] == "/usr/lib/x86_64-linux-gnu"

    def test_includes_plain_usr_lib_fallback(self):
        # Some hosts/CDI runtimes inject into /usr/lib (not the multiarch dir);
        # without this entry the loader falls through to the baked compat lib.
        dirs = CUDA_DRIVER_LD_LIBRARY_PATH.split(":")
        assert "/usr/lib" in dirs
        assert "/usr/lib64" in dirs

    def test_preserves_image_cuda_toolkit_dirs(self):
        # The rest of the CUDA toolkit must still resolve from the image.
        assert "/usr/local/cuda/lib64" in CUDA_DRIVER_LD_LIBRARY_PATH
        assert "/usr/local/nvidia/lib64" in CUDA_DRIVER_LD_LIBRARY_PATH

    def test_no_empty_segments(self):
        dirs = CUDA_DRIVER_LD_LIBRARY_PATH.split(":")
        assert all(d.strip() for d in dirs)
        # absolute paths only
        assert all(d.startswith("/") for d in dirs)


# ---------------------------------------------------------------------------
# create_vllm_job
# ---------------------------------------------------------------------------
class TestVllmJobDriverFix:
    def test_sets_ld_library_path(self):
        job = create_vllm_job(model_id=MODEL)
        env = job["op"]["args"]["env"]
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH

    def test_binds_all_interfaces(self):
        # vLLM must bind 0.0.0.0 or the Nosana FRP proxy cannot reach it.
        cmd = create_vllm_job(model_id=MODEL)["op"]["args"]["cmd"]
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"

    def test_ld_library_path_coexists_with_disable_compat(self):
        # NVIDIA_DISABLE_CUDA_COMPAT=1 alone is insufficient; the LD_LIBRARY_PATH
        # fix must be present even when the disable-compat env is also set.
        job = create_vllm_job(model_id=MODEL, nvidia_disable_cuda_compat="1")
        env = job["op"]["args"]["env"]
        assert env["NVIDIA_DISABLE_CUDA_COMPAT"] == "1"
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH

    def test_ld_library_path_present_without_disable_compat(self):
        # The fix must not depend on the (optional) disable-compat flag.
        job = create_vllm_job(model_id=MODEL, nvidia_disable_cuda_compat="")
        env = job["op"]["args"]["env"]
        assert "NVIDIA_DISABLE_CUDA_COMPAT" not in env
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH

    def test_hf_token_still_set_alongside_fix(self):
        job = create_vllm_job(model_id=MODEL, hf_token="hf_xxx")
        env = job["op"]["args"]["env"]
        assert env["HF_TOKEN"] == "hf_xxx"
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH


# ---------------------------------------------------------------------------
# create_vllm_omni_job (same image family, same fix required)
# ---------------------------------------------------------------------------
class TestVllmOmniJobDriverFix:
    def test_sets_ld_library_path(self):
        job = create_vllm_omni_job(model_id=MODEL)
        env = job["op"]["args"]["env"]
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH

    def test_still_binds_all_interfaces(self):
        cmd = create_vllm_omni_job(model_id=MODEL)["op"]["args"]["cmd"]
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"


# ---------------------------------------------------------------------------
# build_job_definition end-to-end (the actual call path from the adapter)
# ---------------------------------------------------------------------------
class TestBuildJobDefinitionDriverFix:
    def test_vllm_definition_carries_fix(self):
        d = build_job_definition(engine="vllm", model_id=MODEL)
        env = _op_args(d)["env"]
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH
        cmd = _op_args(d)["cmd"]
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"

    def test_vllm_omni_definition_carries_fix(self):
        d = build_job_definition(engine="vllm-omni", model_id=MODEL)
        env = _op_args(d)["env"]
        assert env["LD_LIBRARY_PATH"] == CUDA_DRIVER_LD_LIBRARY_PATH


# ---------------------------------------------------------------------------
# Scoping: the fix targets the vLLM image family only
# ---------------------------------------------------------------------------
class TestFixIsScopedToVllm:
    def test_ollama_job_unaffected(self):
        # Ollama uses its own bundled CUDA runtime and is robust on DePIN; it must
        # NOT get the vLLM-specific LD_LIBRARY_PATH override (avoid over-applying).
        job = create_ollama_job(model_id="llama3")
        env = job["op"]["args"]["env"]
        assert "LD_LIBRARY_PATH" not in env


# ---------------------------------------------------------------------------
# gpu-memory-utilization default (the SECOND Nosana failure mode)
#
# vLLM aborts at startup if free VRAM < gpu_util * total. Community GPUs always
# reserve VRAM for the CUDA context (~0.8 GiB) + co-tenants, so the previous 0.95
# default ("desired 11.04 GiB > free 10.81 GiB") reliably failed the check and the
# container exited (same symptom as 803). The default must leave headroom.
# ---------------------------------------------------------------------------
def _cmd_flag(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class TestGpuMemoryUtilizationDefault:
    def test_vllm_default_leaves_headroom(self):
        cmd = create_vllm_job(model_id=MODEL)["op"]["args"]["cmd"]
        val = float(_cmd_flag(cmd, "--gpu-memory-utilization"))
        # Must be well below 1.0 so free-memory check passes on real GPUs; the
        # previous 0.95 was too aggressive. Pin the intended fleet default.
        assert val == 0.80
        assert val <= 0.90

    def test_vllm_omni_default_leaves_headroom(self):
        cmd = create_vllm_omni_job(model_id=MODEL)["op"]["args"]["cmd"]
        val = float(_cmd_flag(cmd, "--gpu-memory-utilization"))
        assert val == 0.80
        assert val <= 0.90

    def test_explicit_gpu_util_is_respected(self):
        # A user/metadata override must still flow through unchanged.
        cmd = create_vllm_job(model_id=MODEL, gpu_util=0.5)["op"]["args"]["cmd"]
        assert _cmd_flag(cmd, "--gpu-memory-utilization") == "0.5"

    def test_build_job_definition_default_leaves_headroom(self):
        d = build_job_definition(engine="vllm", model_id=MODEL)
        cmd = _op_args(d)["cmd"]
        assert float(_cmd_flag(cmd, "--gpu-memory-utilization")) <= 0.90
