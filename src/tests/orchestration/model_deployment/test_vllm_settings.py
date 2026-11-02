"""vLLM settings saved on a deployment reach the engine, on every path (#431)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from orchestration.messaging.uri_validation import sanitize_config
from orchestration.models.model_deployment import preflight
from orchestration.models.model_deployment.deployment_linker import DeploymentLinker
from orchestration.models.model_deployment.vllm_settings import (
    invalid_settings,
    settings_for_dispatch,
    settings_from,
)

FORM = {
    "max_model_len": 8192,
    "gpu_memory_utilization": 0.8,
    "dtype": "bfloat16",
    "quantization": "fp8",
    "enforce_eager": True,
}


@pytest.fixture
def native(monkeypatch):
    """The model's own context; set .value to change it. Records the lookups."""
    state = SimpleNamespace(value=None, calls=[])

    async def _fetch(model_id, hf_token=None):
        state.calls.append((model_id, hf_token))
        return state.value

    monkeypatch.setattr(preflight, "fetch_native_max_len", _fetch)
    return state


# --- reading a configuration ------------------------------------------------------------

def test_top_level_settings_are_read():
    assert settings_from(FORM) == FORM


def test_a_configuration_stored_as_json_text_is_read():
    assert settings_from(json.dumps(FORM)) == FORM


def test_the_older_gpu_util_name_still_works_and_the_engines_name_wins():
    assert settings_from({"gpu_util": 0.7}) == {"gpu_memory_utilization": 0.7}
    assert settings_from({"gpu_util": 0.7, "gpu_memory_utilization": 0.6}) == {
        "gpu_memory_utilization": 0.6}


def test_numbers_typed_as_text_become_numbers():
    assert settings_from({"max_model_len": "8192", "gpu_memory_utilization": "0.8"}) == {
        "max_model_len": 8192, "gpu_memory_utilization": 0.8}


@pytest.mark.parametrize("cfg", [
    {}, None, "not json", {"dtype": "auto"}, {"kv_cache_dtype": "auto"}, {"quantization": None},
    {"quantization": ""}, {"max_model_len": ""}, {"enforce_eager": False},
    {"trust_remote_code": False},
])
def test_unset_auto_and_off_are_left_for_the_worker_to_decide(cfg):
    """The worker fills only missing keys from its GPU profile."""
    assert settings_from(cfg) == {}


def test_the_nested_block_is_kept_and_wins():
    cfg = {"max_model_len": 8192, "config": {"max_model_len": 4096, "tensor_parallel_size": 2}}
    assert settings_from(cfg) == {"max_model_len": 4096, "tensor_parallel_size": 2}


@pytest.mark.parametrize("cfg,field", [
    ({"max_model_len": "lots"}, "max_model_len"),
    ({"max_model_len": 0}, "max_model_len"),
    ({"max_model_len": -1}, "max_model_len"),
    ({"max_model_len": 1.5}, "max_model_len"),
    ({"max_num_seqs": True}, "max_num_seqs"),
    ({"gpu_memory_utilization": 1.5}, "gpu_memory_utilization"),
    ({"gpu_memory_utilization": 0}, "gpu_memory_utilization"),
    ({"gpu_util": "half"}, "gpu_util"),
    ({"dtype": "--trust-remote-code"}, "dtype"),
    ({"quantization": "fp8 --evil"}, "quantization"),
    ({"kv_cache_dtype": 8}, "kv_cache_dtype"),
    ({"enforce_eager": "maybe"}, "enforce_eager"),
])
def test_a_bad_value_is_reported_and_never_reaches_the_engine(cfg, field):
    errors = invalid_settings(cfg)
    assert len(errors) == 1 and errors[0].startswith(field)
    assert settings_from(cfg) == {}


def test_the_form_values_pass_validation():
    assert invalid_settings(FORM) == []


def test_deploy_and_update_refuse_bad_settings():
    from orchestration.models.model_deployment.deployment_server import (
        _refuse_invalid_vllm_settings,
    )

    _refuse_invalid_vllm_settings(FORM)
    _refuse_invalid_vllm_settings(None)
    with pytest.raises(HTTPException) as exc:
        _refuse_invalid_vllm_settings({"dtype": "--evil"})
    assert exc.value.status_code == 422
    assert "dtype" in exc.value.detail


# --- max_model_len against the model's own context ---------------------------------------

@pytest.mark.asyncio
async def test_max_model_len_is_held_to_the_models_context(native):
    """vLLM refuses to start above it."""
    native.value = 2048
    result = await settings_for_dispatch(FORM, "hf://org/model", "hf_tok")
    assert result["max_model_len"] == 2048
    assert native.calls == [("org/model", "hf_tok")]


@pytest.mark.asyncio
async def test_a_value_within_the_context_is_kept(native):
    native.value = 32768
    assert (await settings_for_dispatch(FORM, "org/model"))["max_model_len"] == 8192


@pytest.mark.asyncio
async def test_an_unknown_context_keeps_the_value(native):
    assert (await settings_for_dispatch(FORM, "org/model"))["max_model_len"] == 8192


@pytest.mark.asyncio
async def test_a_blank_form_still_passes_the_limit_to_the_worker(native):
    """The worker's GPU profile picks max_model_len, and can pick one above it."""
    native.value = 2048
    assert await settings_for_dispatch({}, "org/model") == {"native_max_model_len": 2048}


@pytest.mark.asyncio
async def test_an_unknown_context_adds_nothing(native):
    assert await settings_for_dispatch({}, "org/model") == {}


@pytest.mark.asyncio
async def test_no_huggingface_lookup_for_a_model_stored_elsewhere(native):
    native.value = 1
    result = await settings_for_dispatch(FORM, "s3://bucket/my-model")
    assert result["max_model_len"] == 8192
    assert native.calls == []


# --- the worker path ---------------------------------------------------------------------

@pytest.mark.parametrize("field", list(FORM) + [
    "kv_cache_dtype", "max_num_seqs", "trust_remote_code", "native_max_model_len",
])
def test_the_worker_controller_lets_every_setting_through(field):
    assert field in sanitize_config({field: "x"})


async def _linker_spec(monkeypatch, deploy) -> dict:
    captured = {}

    async def _load(node_id, spec):
        captured.update(spec)
        return SimpleNamespace(status="failed", detail="stop here")

    controller = MagicMock(load_model=_load)
    linker = DeploymentLinker(db_pool=MagicMock(), inventory_repo=MagicMock(),
                              deployment_repo=MagicMock(), worker_controller=controller)
    monkeypatch.setattr(linker, "_mark_failed", AsyncMock())
    await linker._drive_deploy(uuid4(), deploy)
    return captured


def _pending(engine="vllm", configuration=None):
    return {"id": uuid4(), "engine": engine, "gpu_per_replica": 1, "model_name": "m",
            "inference_model": "org/model",
            "configuration": json.dumps(FORM if configuration is None else configuration)}


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["vllm", "vllm-omni", "VLLM"])
async def test_the_linker_sends_the_settings_to_the_worker(monkeypatch, native, engine):
    spec = await _linker_spec(monkeypatch, _pending(engine))
    assert spec["config"] == FORM


@pytest.mark.asyncio
async def test_the_linker_leaves_other_engines_alone(monkeypatch, native):
    spec = await _linker_spec(monkeypatch, _pending("inferia-diffusion", {
        "max_model_len": 8192, "config": {"model_type": "image_generation"}}))
    assert spec["config"] == {"model_type": "image_generation"}


@pytest.mark.asyncio
async def test_the_linker_passes_the_models_limit(monkeypatch, native):
    native.value = 2048
    spec = await _linker_spec(monkeypatch, _pending(configuration={}))
    assert spec["config"] == {"native_max_model_len": 2048}


@pytest.mark.asyncio
async def test_the_linker_sends_nothing_extra_when_nothing_was_set(monkeypatch, native):
    """So the worker's GPU profile decides."""
    spec = await _linker_spec(monkeypatch, _pending(configuration={}))
    assert spec["config"] == {}


def test_a_real_quantization_name_with_a_dash_is_accepted():
    assert invalid_settings({"quantization": "compressed-tensors"}) == []
    assert settings_from({"quantization": "compressed-tensors"}) == {"quantization": "compressed-tensors"}
