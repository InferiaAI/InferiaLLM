import json

import pytest

from orchestration.repositories import model_deployment_repo as repo

CONFIG = {"model": "gpt-4o", "api_key": "sk-secret", "provider": "openai"}


@pytest.fixture(autouse=True)
def _require_key():
    if repo._fernet is None:
        pytest.skip("SECRET_ENCRYPTION_KEY not configured")


class TestWhatReachesTheColumn:

    def test_the_key_is_not_in_the_stored_value(self):
        stored = repo._encrypt_configuration(CONFIG)
        assert "sk-secret" not in stored
        assert "api_key" not in stored

    def test_it_is_the_envelope_the_gateway_writes(self):
        stored = json.loads(repo._encrypt_configuration(CONFIG))
        assert set(stored) == {"data"}
        assert isinstance(stored["data"], str)

    def test_a_json_string_is_accepted_too(self):
        stored = repo._encrypt_configuration(json.dumps(CONFIG))
        assert "sk-secret" not in stored

    def test_none_stays_none(self):
        assert repo._encrypt_configuration(None) is None


class TestReadingItBack:

    def test_round_trip_returns_the_plaintext_json_string(self):
        row = {"configuration": repo._encrypt_configuration(CONFIG)}
        assert json.loads(repo._decrypted(row)["configuration"]) == CONFIG

    def test_callers_still_receive_a_string(self):
        # controller.py calls json.loads on it, so a dict would break it.
        row = {"configuration": repo._encrypt_configuration(CONFIG)}
        assert isinstance(repo._decrypted(row)["configuration"], str)


class TestRowsWrittenBeforeThis:
    """Both sides tolerate plaintext, which is why no migration is needed."""

    def test_a_plaintext_json_string_is_untouched(self):
        row = {"configuration": json.dumps(CONFIG)}
        assert json.loads(repo._decrypted(row)["configuration"]) == CONFIG

    def test_an_unparseable_value_is_untouched(self):
        row = {"configuration": "not json"}
        assert repo._decrypted(row)["configuration"] == "not json"

    def test_an_empty_configuration_is_untouched(self):
        assert repo._decrypted({"configuration": None})["configuration"] is None

    def test_a_row_without_the_column(self):
        assert repo._decrypted({"state": "RUNNING"}) == {"state": "RUNNING"}


class TestInteropWithTheGateway:
    """The two services share the column, so the envelope must match."""

    def test_the_gateway_can_read_what_orchestration_writes(self):
        from api_gateway.db.security import encryption_service

        stored = json.loads(repo._encrypt_configuration(CONFIG))
        assert encryption_service.decrypt_json(stored["data"]) == CONFIG

    def test_orchestration_can_read_what_the_gateway_writes(self):
        from api_gateway.db.security import EncryptedJSON

        stored = EncryptedJSON().process_bind_param(CONFIG, None)
        row = repo._decrypted({"configuration": json.dumps(stored)})
        assert json.loads(row["configuration"]) == CONFIG
