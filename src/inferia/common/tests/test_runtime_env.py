"""Tests for common.runtime_env — CP-side runtime environment detector."""

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from inferia.common import runtime_env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, text: str = "", json_data: dict | None = None):
    """Build a minimal synchronous httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    return resp


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

class TestEnvOverride:
    """INFERIA_RUNTIME_ENV env var wins over everything."""

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
        runtime_env._CACHE.clear()
        assert runtime_env.detect_runtime_env() == "aws-ec2"

    def test_env_override_k8s(self, monkeypatch):
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "k8s")
        runtime_env._CACHE.clear()
        assert runtime_env.detect_runtime_env() == "k8s"

    def test_env_override_unknown(self, monkeypatch):
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "unknown")
        runtime_env._CACHE.clear()
        assert runtime_env.detect_runtime_env() == "unknown"

    def test_env_override_truncated_to_64(self, monkeypatch):
        """Values longer than 64 chars are silently truncated."""
        long_value = "a" * 100
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", long_value)
        runtime_env._CACHE.clear()
        result = runtime_env.detect_runtime_env()
        assert result == "a" * 64

    def test_env_override_no_imds_call(self, monkeypatch):
        """When env var is set, httpx must never be called."""
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
        runtime_env._CACHE.clear()
        with patch("httpx.Client") as mock_client:
            runtime_env.detect_runtime_env()
            mock_client.assert_not_called()


class TestNoEnvNoIMDS:
    """When env var is absent and IMDS is unreachable, returns 'local'."""

    def test_no_env_no_imds_returns_local(self, monkeypatch):
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://127.0.0.1:1")
        runtime_env._CACHE.clear()
        # The real httpx.Client will raise a connection error at port 1.
        assert runtime_env.detect_runtime_env() == "local"

    def test_exception_in_imds_returns_local(self, monkeypatch):
        """Any exception from httpx → 'local'."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds-fail")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(side_effect=Exception("boom"))
        mock_client_instance.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "local"

    def test_put_non_200_returns_local(self, monkeypatch):
        """Token endpoint returning non-200 → 'local'."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(401, text="denied")

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "local"

    def test_get_non_200_returns_local(self, monkeypatch):
        """Document endpoint returning non-200 → 'local'."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="tok")
        mock_client_instance.get.return_value = _make_response(404)

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "local"

    def test_get_200_no_instance_id_returns_local(self, monkeypatch):
        """Document 200 but missing 'instanceId' → 'local'."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="tok")
        mock_client_instance.get.return_value = _make_response(200, json_data={"region": "us-east-1"})

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "local"


class TestIMDSSuccess:
    """Full happy-path via mocked httpx.Client."""

    def test_imds_success(self, monkeypatch):
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="tok")
        mock_client_instance.get.return_value = _make_response(
            200,
            json_data={"instanceId": "i-1", "region": "us-east-1", "availabilityZone": "us-east-1a"},
        )

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "aws-ec2"

    def test_imds_token_header_sent(self, monkeypatch):
        """The IMDSv2 TTL header must be sent with the PUT."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="mytoken")
        mock_client_instance.get.return_value = _make_response(
            200,
            json_data={"instanceId": "i-abc"},
        )

        with patch("httpx.Client", return_value=mock_client_instance):
            runtime_env.detect_runtime_env()

        # Verify the PUT was called with the TTL header
        put_kwargs = mock_client_instance.put.call_args
        assert put_kwargs is not None
        headers = put_kwargs.kwargs.get("headers", {}) or (put_kwargs.args[1] if len(put_kwargs.args) > 1 else {})
        assert "X-aws-ec2-metadata-token-ttl-seconds" in headers

    def test_imds_get_uses_token(self, monkeypatch):
        """The GET must include the token returned by PUT."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="secret-token")
        mock_client_instance.get.return_value = _make_response(
            200,
            json_data={"instanceId": "i-xyz"},
        )

        with patch("httpx.Client", return_value=mock_client_instance):
            runtime_env.detect_runtime_env()

        get_kwargs = mock_client_instance.get.call_args
        headers = get_kwargs.kwargs.get("headers", {})
        assert headers.get("X-aws-ec2-metadata-token") == "secret-token"


class TestCaching:
    """_CACHE ensures detect_runtime_env is called only once."""

    def test_cached(self, monkeypatch):
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
        runtime_env._CACHE.clear()
        runtime_env.detect_runtime_env()
        runtime_env.detect_runtime_env()
        runtime_env.detect_runtime_env()
        assert runtime_env._CACHE["env"] == "aws-ec2"

    def test_cache_prevents_repeated_imds_calls(self, monkeypatch):
        """httpx.Client should only be constructed once even across 3 calls."""
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="tok")
        mock_client_instance.get.return_value = _make_response(
            200,
            json_data={"instanceId": "i-1"},
        )

        with patch("httpx.Client", return_value=mock_client_instance) as mock_cls:
            runtime_env.detect_runtime_env()
            runtime_env.detect_runtime_env()
            runtime_env.detect_runtime_env()

        assert mock_cls.call_count == 1

    def test_cache_cleared_redetects(self, monkeypatch):
        """After _CACHE.clear(), detection runs fresh."""
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "local")
        runtime_env._CACHE.clear()
        runtime_env.detect_runtime_env()
        assert runtime_env._CACHE["env"] == "local"

        # Change env var and clear cache → should re-detect
        monkeypatch.setenv("INFERIA_RUNTIME_ENV", "k8s")
        runtime_env._CACHE.clear()
        result = runtime_env.detect_runtime_env()
        assert result == "k8s"
        assert runtime_env._CACHE["env"] == "k8s"


class TestDefaultIMDSURL:
    """When INFERIA_CLOUDENV_IMDS_URL is not set, uses the AWS link-local default."""

    def test_default_url_is_aws_link_local(self, monkeypatch):
        monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
        monkeypatch.delenv("INFERIA_CLOUDENV_IMDS_URL", raising=False)
        runtime_env._CACHE.clear()

        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.put.return_value = _make_response(200, text="tok")
        mock_client_instance.get.return_value = _make_response(
            200,
            json_data={"instanceId": "i-default"},
        )

        with patch("httpx.Client", return_value=mock_client_instance):
            result = runtime_env.detect_runtime_env()

        assert result == "aws-ec2"
        put_url = mock_client_instance.put.call_args.args[0]
        assert put_url.startswith("http://169.254.169.254")
