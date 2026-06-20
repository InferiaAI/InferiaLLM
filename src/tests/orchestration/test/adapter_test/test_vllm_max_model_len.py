"""max_model_len handling for vLLM Nosana jobs.

Regression for the crash where every small-context model (e.g. facebook/opt-125m,
native context 2048) failed to deploy on Nosana: the job builder hardcoded
``--max-model-len 8192``, and vLLM v0.16 HARD-ERRORS at config creation when the
requested context exceeds the model's native ``max_position_embeddings`` — the
container then crashes during model load ("endpoint never served"). Large-context
models (Qwen3, native 40960) were unaffected, which is why only some deploys
crashed.

Fix: omit ``--max-model-len`` unless a value is known, and clamp any value down
to the model's native context.
"""

from unittest.mock import MagicMock, patch

import pytest

# nosana_adapter reads several settings at import time; a MagicMock resolves
# them all. internal_api_key is pinned so the builder's auth header is a string.
_mock_settings = MagicMock()
_mock_settings.internal_api_key = "test-key"

with patch("orchestration.config.settings", _mock_settings):
    from providers.nosana.job_builder import (
        create_vllm_job,
        create_vllm_omni_job,
        build_job_definition,
    )
    from providers.nosana.nosana_adapter import (
        _clamp_max_model_len,
        DEFAULT_VLLM_MAX_MODEL_LEN,
    )

MODEL = "facebook/opt-125m"


def _cmd(job_or_def):
    if "op" in job_or_def:
        return job_or_def["op"]["args"]["cmd"]
    return job_or_def["ops"][0]["args"]["cmd"]


# --------------------------- builder: flag omission -------------------------

class TestBuilderMaxModelLenFlag:
    def test_omitted_by_default(self):
        # Default (None) -> no --max-model-len so vLLM derives the native context.
        assert "--max-model-len" not in _cmd(create_vllm_job(model_id=MODEL))

    def test_present_when_set(self):
        cmd = _cmd(create_vllm_job(model_id=MODEL, max_model_len=2048))
        assert "--max-model-len" in cmd
        assert cmd[cmd.index("--max-model-len") + 1] == "2048"

    def test_omni_omitted_by_default(self):
        assert "--max-model-len" not in _cmd(create_vllm_omni_job(model_id=MODEL))

    def test_omni_present_when_set(self):
        cmd = _cmd(create_vllm_omni_job(model_id=MODEL, max_model_len=4096))
        assert cmd[cmd.index("--max-model-len") + 1] == "4096"

    def test_build_job_definition_omits_when_none(self):
        # build_job_definition drops None kwargs, so passing None omits the flag.
        cmd = _cmd(build_job_definition(engine="vllm", model_id=MODEL, max_model_len=None))
        assert "--max-model-len" not in cmd

    def test_build_job_definition_passes_int(self):
        cmd = _cmd(build_job_definition(engine="vllm", model_id=MODEL, max_model_len=2048))
        assert cmd[cmd.index("--max-model-len") + 1] == "2048"

    def test_other_flags_still_present(self):
        # Removing --max-model-len must not disturb the rest of the command.
        cmd = _cmd(create_vllm_job(model_id=MODEL, gpu_util=0.8))
        assert "--gpu-memory-utilization" in cmd
        assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.8"
        assert "--served-model-name" in cmd


# ------------------------------ clamp logic ---------------------------------

class TestClampMaxModelLen:
    def test_small_model_default_capped_to_native(self):
        # opt-125m: no request, native 2048 -> 2048 (the bug case).
        assert _clamp_max_model_len(None, 2048) == 2048

    def test_large_model_default_uses_cap(self):
        # Qwen3: no request, native 40960 -> 8192 (unchanged from old behavior).
        assert _clamp_max_model_len(None, 40960) == DEFAULT_VLLM_MAX_MODEL_LEN

    def test_unknown_native_no_request_returns_none(self):
        # Native unknown + no request -> omit the flag, let vLLM derive.
        assert _clamp_max_model_len(None, None) is None

    def test_requested_clamped_down_to_native(self):
        assert _clamp_max_model_len(4096, 2048) == 2048

    def test_requested_under_native_kept(self):
        assert _clamp_max_model_len(4096, 40960) == 4096

    def test_requested_kept_when_native_unknown(self):
        assert _clamp_max_model_len(16384, None) == 16384

    def test_requested_equal_native(self):
        assert _clamp_max_model_len(2048, 2048) == 2048

    @pytest.mark.parametrize("native", [2048, 8192, 40960, 128000])
    def test_never_exceeds_native(self, native):
        for requested in (None, 1024, 8192, 999999):
            eff = _clamp_max_model_len(requested, native)
            if eff is not None:
                assert eff <= native
