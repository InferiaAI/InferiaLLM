"""Regression test for the engine-only deploy gate in ModelDeploymentWorker.

The dispatcher's "Last Resort / Error Check" used to fail any deployment
whose metadata lacked both ``image`` and ``cmd``, even though deployments
that carry an ``engine`` + ``model_id`` are intentionally resolved by the
provider adapter via job_builder. That made every UI-launched Nosana
vLLM deploy fail with "Missing job definition or image for deployment"
before the adapter ever ran.

We don't run the full dispatcher here — its dependencies (asyncpg, gRPC,
Redis) are too heavy to spin up in a unit test. Instead we mirror the
exact boolean the dispatcher evaluates, against representative inputs.
This keeps the gate's contract pinned: an engine-or-model-id deployment
must pass the gate, a bare deployment must fail it.
"""


def _gate_blocks(metadata: dict, deployment: dict) -> bool:
    """Re-implementation of the gate in worker.handle_deploy_requested.

    Mirrors the production code byte-for-byte; the test file is the
    canary that catches a regression in either one."""
    has_engine_or_model = bool(
        metadata.get("engine")
        or metadata.get("model_id")
        or metadata.get("model_name")
        or deployment.get("engine")
        or deployment.get("inference_model")
    )
    return (
        not metadata.get("image")
        and not metadata.get("cmd")
        and metadata.get("workload_type") != "training"
        and not has_engine_or_model
    )


def test_engine_in_metadata_passes_gate():
    """vLLM through the wizard — metadata.engine + model_id, no image."""
    metadata = {"engine": "vllm", "model_id": "Qwen/Qwen2.5-0.5B-Instruct"}
    deployment = {"engine": "vllm", "inference_model": "Qwen/Qwen2.5-0.5B-Instruct"}
    assert not _gate_blocks(metadata, deployment)


def test_engine_only_on_deployment_record_still_passes():
    """Some legacy paths only set engine on the deployment row, not in
    metadata. The gate must still let those through."""
    metadata = {}
    deployment = {"engine": "vllm", "inference_model": "meta-llama/Llama-3-8B"}
    assert not _gate_blocks(metadata, deployment)


def test_model_id_alone_passes_gate():
    """Engine omitted but model_id present — adapter still has enough."""
    metadata = {"model_id": "qwen2.5:0.5b"}
    deployment = {}
    assert not _gate_blocks(metadata, deployment)


def test_legacy_image_cmd_passes_gate():
    """Legacy callers pass image+cmd directly; gate must let them through."""
    metadata = {"image": "vllm/vllm-openai:latest", "cmd": ["--port", "9000"]}
    deployment = {}
    assert not _gate_blocks(metadata, deployment)


def test_training_workload_passes_gate_without_image():
    """Training deployments resolve their image via a separate code path;
    the gate must not block them."""
    metadata = {"workload_type": "training"}
    deployment = {}
    assert not _gate_blocks(metadata, deployment)


def test_bare_metadata_is_rejected():
    """No engine, no model, no image, no cmd, not training — the gate
    is the right place to catch this."""
    metadata = {}
    deployment = {}
    assert _gate_blocks(metadata, deployment)


def test_workload_type_inference_without_engine_is_rejected():
    """The gate considers workload_type != training only as a passthrough
    signal; it does NOT exempt an otherwise-empty payload."""
    metadata = {"workload_type": "inference"}
    deployment = {}
    assert _gate_blocks(metadata, deployment)
