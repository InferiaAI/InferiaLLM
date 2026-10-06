"""Every deployment is created with an engine credential of its own.

Three paths talked to a deployment's engine and each fell back to
`settings.internal_api_key` when the configuration held no key: the health
check, the inference proxy, and the Nosana job builder. That key authenticates
`/internal/`, which includes the route returning every provider credential
unmasked, and the endpoint it reached belongs to whoever created the deployment.

Removing the three fallbacks only works if every deployment has a key, so this
is the half that makes the other half safe. See #365.
"""
import json

from orchestration.repositories.model_deployment_repo import _with_engine_key


def _config(configuration):
    return json.loads(_with_engine_key(configuration))


class TestAKeyIsAlwaysPresent:

    def test_no_configuration_at_all(self):
        assert _config(None)["api_key"]

    def test_an_empty_dict(self):
        assert _config({})["api_key"]

    def test_a_json_string(self):
        assert _config('{"model_id": "qwen"}')["api_key"]

    def test_unparseable_configuration_does_not_raise(self):
        """A bad row must not leave a deployment falling back to the service key."""
        assert _config("not json at all")["api_key"]


class TestWhatIsAlreadyThere:

    def test_other_fields_survive(self):
        got = _config({"model_id": "qwen", "env": {"HF_TOKEN": "hf_x"}})
        assert got["model_id"] == "qwen"
        assert got["env"] == {"HF_TOKEN": "hf_x"}

    def test_a_json_string_keeps_its_fields(self):
        got = _config('{"model_id": "qwen"}')
        assert got["model_id"] == "qwen"

    def test_an_existing_key_is_never_replaced(self):
        """An external provider's row carries the customer's own credential."""
        got = _config({"api_key": "sk-customer-owned"})
        assert got["api_key"] == "sk-customer-owned"


class TestExternalDeploymentsAreLeftAlone:
    """An external workload is someone else's endpoint plus their credential."""

    def test_no_key_is_generated_for_one(self):
        got = _config({"workload_type": "external"})
        assert "api_key" not in got

    def test_their_own_key_still_survives(self):
        got = _config({"workload_type": "external", "api_key": "sk-theirs"})
        assert got["api_key"] == "sk-theirs"

    def test_every_other_workload_type_gets_one(self):
        assert _config({"workload_type": "inference"})["api_key"]

    def test_an_unmarked_row_gets_one(self):
        """Only the external path sets workload_type, so absence means ours."""
        assert _config({"model_id": "qwen"})["api_key"]


class TestTheKeyItself:

    def test_it_is_not_guessable(self):
        key = _config(None)["api_key"]
        assert key.startswith("dep_")
        assert len(key) > 32

    def test_two_deployments_do_not_share_one(self):
        assert _config(None)["api_key"] != _config(None)["api_key"]

    def test_the_result_is_json_the_column_can_take(self):
        """The column is jsonb and the caller passes a string."""
        out = _with_engine_key({"model_id": "qwen"})
        assert isinstance(out, str)
        assert json.loads(out)["model_id"] == "qwen"
