import json

import pytest

from orchestration.models.model_deployment.deployment_server import (
    _SECRET_CONFIG_KEYS,
    _with_stored_secrets,
    _without_secrets,
)

STORED = {
    "api_key": "sk-live-secret",
    "model": "gpt-4o",
    "provider": "openai",
}


def _round_trip(stored, edits=None):
    """What a client sends back after reading the deployment and editing it."""
    seen = _without_secrets(stored)
    seen.update(edits or {})
    return _with_stored_secrets(seen, stored)


class TestAReadEditWriteRoundTrip:
    """Reads strip secrets, so a client echoing what it saw would blank them."""

    def test_the_credential_survives(self):
        assert _round_trip(STORED)["api_key"] == "sk-live-secret"

    def test_an_edit_is_still_applied(self):
        got = _round_trip(STORED, {"model": "gpt-4o-mini"})
        assert got["model"] == "gpt-4o-mini"
        assert got["api_key"] == "sk-live-secret"

    def test_a_new_key_is_added(self):
        got = _round_trip(STORED, {"autoscale_p95_seconds": 2})
        assert got["autoscale_p95_seconds"] == 2
        assert got["api_key"] == "sk-live-secret"

    @pytest.mark.parametrize("secret", sorted(_SECRET_CONFIG_KEYS))
    def test_every_stripped_key_is_restored(self, secret):
        stored = {secret: "value", "model": "m"}
        assert _round_trip(stored)[secret] == "value"


class TestWhatItDoesNotDo:

    def test_an_explicit_new_value_wins(self):
        got = _with_stored_secrets({"api_key": "sk-new"}, STORED)
        assert got["api_key"] == "sk-new", "the caller set it deliberately"

    def test_an_explicit_empty_value_is_respected(self):
        got = _with_stored_secrets({"api_key": ""}, STORED)
        assert got["api_key"] == "", "clearing a credential must still work"

    def test_ordinary_keys_can_still_be_removed(self):
        got = _with_stored_secrets({"api_key": "sk-x"}, STORED)
        assert "model" not in got, "only secrets are restored"


class TestInputsItHasToTolerate:

    def test_a_stored_json_string(self):
        got = _with_stored_secrets({"model": "m"}, json.dumps(STORED))
        assert got["api_key"] == "sk-live-secret"

    @pytest.mark.parametrize("stored", [None, "", "not json", 42, [], {}])
    def test_anything_unusable_leaves_the_incoming_alone(self, stored):
        assert _with_stored_secrets({"model": "m"}, stored) == {"model": "m"}
