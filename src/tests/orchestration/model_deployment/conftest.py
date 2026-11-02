import pytest


@pytest.fixture(autouse=True)
def _model_context_unknown(monkeypatch):
    """Every vLLM dispatch asks Hugging Face for the model's context; not from tests."""
    async def _fetch(model_id, hf_token=None):
        return None

    monkeypatch.setattr(
        "orchestration.models.model_deployment.preflight.fetch_native_max_len", _fetch,
    )
