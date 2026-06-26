"""Op-id sanitization for Nosana job definitions.

Nosana's POST /deployments/create validates each container op "id" against
``^[A-Za-z0-9_-]+$``. HF repo ids ("Qwen/Qwen3-0.6B", "meta-llama/Llama-3.1-...")
carry "/" and "." — both rejected — which previously failed every namespaced or
dotted vLLM deploy at submission with an opaque HTTP 400. These tests pin the
sanitizer contract and assert the two builder sites that derive an op id from
model_id (vLLM, vLLM-Omni) now always emit a valid slug.
"""
import re
from unittest.mock import patch

# job_builder computes INTERNAL_API_KEY from settings AT IMPORT, so patch first.
_mock_settings = type("Settings", (), {"internal_api_key": "test-key"})()
with patch("orchestration.config.settings", _mock_settings):
    from providers.nosana.job_builder import (
        _sanitize_op_id,
        _OPID_MAX_LEN,
        create_vllm_job,
        create_vllm_omni_job,
        build_job_definition,
    )

# The exact charset Nosana accepts (verified live).
_VALID_OPID = re.compile(r"[A-Za-z0-9_-]+")

# Realistic model ids exercised across the suite, all containing "/" and most "."
REAL_MODEL_IDS = [
    "Qwen/Qwen3-0.6B",
    "meta-llama/Llama-3.1-8B-Instruct",
    "Qwen/Qwen2.5-7B",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "sentence-transformers/all-MiniLM-L6-v2",
    "stabilityai/stable-diffusion-2.1",
]


def _is_valid(op_id: str) -> bool:
    return bool(_VALID_OPID.fullmatch(op_id))


class TestSanitizeOpIdCharset:
    """Disallowed characters are mapped out; allowed ones survive."""

    def test_slash_becomes_dash(self):
        assert _sanitize_op_id("a/b") == "a-b"

    def test_dot_becomes_dash(self):
        # The latent bug the old `.replace('/', '-')` missed.
        assert _sanitize_op_id("a.b") == "a-b"

    def test_slash_and_dot_together(self):
        assert _sanitize_op_id("Qwen/Qwen3-0.6B") == "Qwen-Qwen3-0-6B"

    def test_uppercase_preserved(self):
        # Nosana accepts uppercase (verified live) — do not lowercase.
        assert _sanitize_op_id("QwenModel") == "QwenModel"

    def test_underscore_preserved(self):
        assert _sanitize_op_id("a_b_c") == "a_b_c"

    def test_digits_and_dash_preserved(self):
        assert _sanitize_op_id("qwen3-0-6b") == "qwen3-0-6b"

    def test_misc_specials_mapped(self):
        for ch in [" ", ":", "@", "#", "%", "+", "!", "\\", "*", "(", ")"]:
            out = _sanitize_op_id(f"a{ch}b")
            assert _is_valid(out), (ch, out)
            assert ch not in out


class TestSanitizeOpIdShape:
    """Collapsing, trimming, length capping, and fallback behaviour."""

    def test_consecutive_invalids_collapse_to_single_dash(self):
        assert _sanitize_op_id("a///b") == "a-b"
        assert _sanitize_op_id("a/.//b") == "a-b"

    def test_leading_separators_trimmed(self):
        assert _sanitize_op_id("/a") == "a"
        assert _sanitize_op_id("._a") == "a"

    def test_trailing_separators_trimmed(self):
        assert _sanitize_op_id("a/") == "a"
        assert _sanitize_op_id("a_.") == "a"

    def test_length_overflow_truncated(self):
        out = _sanitize_op_id("a" * 200)
        assert len(out) == _OPID_MAX_LEN
        assert out == "a" * _OPID_MAX_LEN
        assert _is_valid(out)

    def test_length_overflow_real_long_repo_id(self):
        long_id = "some-org/" + ("very-long-model-name-" * 10) + "v1.0"
        out = _sanitize_op_id(long_id)
        assert len(out) <= _OPID_MAX_LEN
        assert _is_valid(out)

    def test_truncation_boundary_dash_is_trimmed(self):
        # "."(->"-") lands exactly at the truncation boundary; the resulting
        # trailing dash must be stripped so the slug never ends in a separator.
        raw = "a" * (_OPID_MAX_LEN - 1) + "." + "bbb"
        out = _sanitize_op_id(raw)
        assert out == "a" * (_OPID_MAX_LEN - 1)
        assert not out.endswith("-")
        assert _is_valid(out)

    def test_all_invalid_input_uses_fallback(self):
        assert _sanitize_op_id("///...") == "service"

    def test_empty_input_uses_fallback(self):
        assert _sanitize_op_id("") == "service"

    def test_none_input_uses_fallback(self):
        # raw or "" guards against None reaching the regex.
        assert _sanitize_op_id(None) == "service"

    def test_custom_fallback_respected(self):
        assert _sanitize_op_id("", fallback="vllm") == "vllm"
        assert _sanitize_op_id("@@@", fallback="omni") == "omni"

    def test_fallback_itself_is_valid(self):
        assert _is_valid(_sanitize_op_id(""))


class TestSanitizeOpIdInvariants:
    """Properties that must hold for every input."""

    def test_output_always_valid_charset(self):
        samples = REAL_MODEL_IDS + [
            "", "/", "...", "a", "A_B-c", "x" * 500,
            "org/model.v1.2.3", "weird name with spaces", "emoji-🚀-model",
        ]
        for raw in samples:
            out = _sanitize_op_id(raw)
            assert _is_valid(out), (raw, out)
            assert 0 < len(out) <= _OPID_MAX_LEN

    def test_deterministic(self):
        # The Nosana SDK derives the service-URL hash from the op id; it must be
        # stable across re-resolves of the same deployment.
        for raw in REAL_MODEL_IDS:
            assert _sanitize_op_id(raw) == _sanitize_op_id(raw)


class TestVllmBuilderOpId:
    """The primary bug: create_vllm_job op id derived from raw model_id."""

    def test_vllm_op_id_is_valid_for_namespaced_dotted_model(self):
        op = create_vllm_job(model_id="Qwen/Qwen3-0.6B")["op"]
        assert "/" not in op["id"] and "." not in op["id"]
        assert _is_valid(op["id"])

    def test_vllm_op_id_valid_across_real_models(self):
        for mid in REAL_MODEL_IDS:
            op = create_vllm_job(model_id=mid)["op"]
            assert _is_valid(op["id"]), (mid, op["id"])

    def test_vllm_op_id_derived_from_model(self):
        # Sanity: the slug still reflects the model for debuggability.
        op = create_vllm_job(model_id="Qwen/Qwen3-0.6B")["op"]
        assert op["id"] == "Qwen-Qwen3-0-6B"

    def test_build_job_definition_vllm_op_id_valid(self):
        job = build_job_definition(
            engine="vllm", model_id="meta-llama/Llama-3.1-8B-Instruct"
        )
        op_id = job["ops"][0]["id"]
        assert _is_valid(op_id)
        assert op_id == "meta-llama-Llama-3-1-8B-Instruct"


class TestVllmOmniBuilderOpId:
    """The latent omni bug: old replace('/','-') left '.' in dotted models."""

    def test_omni_op_id_strips_dot(self):
        op = create_vllm_omni_job(model_id="Qwen/Qwen2.5-7B")["op"]
        assert "." not in op["id"] and "/" not in op["id"]
        assert _is_valid(op["id"])

    def test_omni_op_id_keeps_prefix(self):
        op = create_vllm_omni_job(model_id="Qwen/Qwen2.5-7B")["op"]
        assert op["id"].startswith("vllm-omni-")
        assert op["id"] == "vllm-omni-Qwen-Qwen2-5-7B"

    def test_build_job_definition_omni_op_id_valid(self):
        for mid in REAL_MODEL_IDS:
            job = build_job_definition(engine="vllm-omni", model_id=mid)
            assert _is_valid(job["ops"][0]["id"]), (mid, job["ops"][0]["id"])
