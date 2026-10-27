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

    def test_a_blank_value_keeps_the_stored_one(self):
        """The edit form starts blank for a secret it was never shown, so blank
        means unchanged (CLAUDE.md, 2026-06-10). Replace a key to change it."""
        got = _with_stored_secrets({"api_key": ""}, STORED)
        assert got["api_key"] == STORED["api_key"]

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


class TestEnvSecrets:
    """/status strips credential env vars, so an edit must not blank them."""

    STORED = {"env": {"HF_TOKEN": "hf_x", "MAX_MODEL_LEN": "4096"}}

    def test_a_stripped_env_secret_survives_an_edit(self):
        got = _with_stored_secrets({"env": {"MAX_MODEL_LEN": "8192"}}, self.STORED)
        assert got["env"] == {"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_x"}

    def test_it_survives_when_env_was_not_sent(self):
        got = _with_stored_secrets({"model": "m"}, self.STORED)
        assert got == {"model": "m", "env": {"HF_TOKEN": "hf_x"}}

    def test_a_new_env_secret_wins(self):
        got = _with_stored_secrets({"env": {"HF_TOKEN": "hf_new"}}, self.STORED)
        assert got["env"]["HF_TOKEN"] == "hf_new"

    def test_ordinary_env_vars_can_still_be_removed(self):
        got = _with_stored_secrets({"env": {}}, self.STORED)
        assert got["env"] == {"HF_TOKEN": "hf_x"}


def test_saving_a_training_deploy_keeps_its_token():
    """DeploymentConfig.tsx always sends hf_token, blank unless retyped."""
    got = _with_stored_secrets({"git_repo": "r", "hf_token": ""}, {"hf_token": "hf_REAL"})
    assert got["hf_token"] == "hf_REAL"


def test_a_blank_env_secret_keeps_the_stored_one():
    got = _with_stored_secrets({"env": {"HF_TOKEN": ""}}, {"env": {"HF_TOKEN": "hf_REAL"}})
    assert got["env"]["HF_TOKEN"] == "hf_REAL"


def test_a_stripped_top_level_token_survives_an_edit():
    got = _with_stored_secrets({"git_repo": "r2"}, {"git_repo": "r", "hf_token": "hf_x"})
    assert got == {"git_repo": "r2", "hf_token": "hf_x"}
