"""The health check must never send the platform's internal key outward.

`check_health` probes `<endpoint>/v1/models`, and for an external deployment
that endpoint is chosen by whoever created it. A fallback to
`settings.internal_api_key` therefore handed the service credential to them.
These tests pin the header down so the fallback cannot come back.
"""
from types import SimpleNamespace

from api_gateway.config import settings
from api_gateway.gateway.router import _health_check_headers


def _deployment(configuration):
    return SimpleNamespace(
        id="dep-1", endpoint="https://attacker.example/v1", configuration=configuration
    )


class TestKeyIsTaken:
    """The deployment's own credential is what gets sent."""

    def test_api_key(self):
        got = _health_check_headers(_deployment({"api_key": "sk-abc"}))
        assert got == {"Authorization": "Bearer sk-abc"}

    def test_key(self):
        got = _health_check_headers(_deployment({"key": "sk-abc"}))
        assert got == {"Authorization": "Bearer sk-abc"}

    def test_token(self):
        got = _health_check_headers(_deployment({"token": "sk-abc"}))
        assert got == {"Authorization": "Bearer sk-abc"}

    def test_api_key_wins_over_the_others(self):
        got = _health_check_headers(
            _deployment({"api_key": "first", "key": "second", "token": "third"})
        )
        assert got == {"Authorization": "Bearer first"}


class TestNoKeyMeansNoHeader:
    """Without a credential of its own, the probe goes out unauthenticated."""

    def test_configuration_is_none(self):
        assert _health_check_headers(_deployment(None)) == {}

    def test_configuration_is_empty(self):
        assert _health_check_headers(_deployment({})) == {}

    def test_configuration_has_no_key_fields(self):
        assert _health_check_headers(_deployment({"model": "llama-3"})) == {}


def test_the_internal_key_is_never_sent(monkeypatch):
    """The regression this file exists for.

    The endpoint belongs to whoever created the deployment, so a key that
    authenticates Inferia's own /internal/ routes must not travel to it.
    """
    monkeypatch.setattr(settings, "internal_api_key", "internal-secret", raising=False)

    for configuration in (None, {}, {"model": "llama-3"}):
        got = _health_check_headers(_deployment(configuration))
        assert "internal-secret" not in str(got), configuration
        assert got == {}
