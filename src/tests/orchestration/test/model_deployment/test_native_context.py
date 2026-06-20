"""Native context-window resolution used to clamp vLLM max_model_len."""

import httpx
import pytest

from orchestration.models.model_deployment.preflight import (
    _native_context_from_config,
    fetch_native_max_len,
    check_context_length,
)


class TestNativeContextFromConfig:
    def test_max_position_embeddings(self):
        assert _native_context_from_config({"max_position_embeddings": 2048}) == 2048

    def test_max_sequence_length(self):
        assert _native_context_from_config({"max_sequence_length": 4096}) == 4096

    def test_seq_length(self):
        assert _native_context_from_config({"seq_length": 1024}) == 1024

    def test_n_positions(self):
        assert _native_context_from_config({"n_positions": 512}) == 512

    def test_nested_text_config(self):
        # Multimodal (e.g. Gemma 3): context lives under text_config.
        cfg = {"model_type": "gemma3", "text_config": {"max_position_embeddings": 32768}}
        assert _native_context_from_config(cfg) == 32768

    def test_top_level_wins_over_nested(self):
        cfg = {"max_position_embeddings": 8192, "text_config": {"max_position_embeddings": 4096}}
        assert _native_context_from_config(cfg) == 8192

    def test_missing_returns_none(self):
        assert _native_context_from_config({"hidden_size": 768}) is None

    def test_zero_is_invalid(self):
        assert _native_context_from_config({"max_position_embeddings": 0}) is None

    def test_non_int_ignored(self):
        assert _native_context_from_config({"max_position_embeddings": "2048"}) is None

    def test_none_and_non_dict(self):
        assert _native_context_from_config(None) is None
        assert _native_context_from_config("nope") is None


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._resp


@pytest.mark.asyncio
class TestFetchNativeMaxLen:
    async def test_success(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _Client(resp=_Resp(200, {"max_position_embeddings": 2048})),
        )
        assert await fetch_native_max_len("facebook/opt-125m") == 2048

    async def test_nested_success(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _Client(resp=_Resp(200, {"text_config": {"seq_length": 8192}})),
        )
        assert await fetch_native_max_len("x/y") == 8192

    async def test_non_200_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **k: _Client(resp=_Resp(401, None))
        )
        assert await fetch_native_max_len("gated/model", hf_token="t") is None

    async def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _Client(exc=httpx.ConnectError("boom")),
        )
        assert await fetch_native_max_len("x/y") is None

    async def test_missing_field_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _Client(resp=_Resp(200, {"hidden_size": 1})),
        )
        assert await fetch_native_max_len("x/y") is None


class TestCheckContextLengthStillWorks:
    """The refactor must preserve check_context_length behavior."""

    def test_within_limit_ok(self):
        hf = {"config": {"max_position_embeddings": 8192}}
        assert check_context_length(hf, 4096).ok

    def test_exceeds_limit_fails(self):
        hf = {"config": {"max_position_embeddings": 2048}}
        res = check_context_length(hf, 8192)
        assert not res.ok and "native" in res.error

    def test_skipped_when_no_max(self):
        assert check_context_length({"config": {}}, 8192).skipped

    def test_skipped_when_no_request(self):
        assert check_context_length({"config": {"max_position_embeddings": 2048}}, None).skipped
