"""Tests for DB-level Fernet encryption — security layer."""

import json
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet

from inferia.services.api_gateway.db.security import EncryptionService


@pytest.fixture
def enc_service():
    """EncryptionService with a real Fernet key."""
    key = Fernet.generate_key().decode()
    with patch("inferia.services.api_gateway.db.security.ENCRYPTION_KEY", key):
        svc = EncryptionService()
    return svc


@pytest.fixture
def no_key_service():
    """EncryptionService with no encryption key."""
    with patch("inferia.services.api_gateway.db.security.ENCRYPTION_KEY", None):
        svc = EncryptionService()
    return svc


class TestEncryptionServiceRoundtrip:
    """Fernet encrypt/decrypt for DB fields."""

    def test_string_roundtrip(self, enc_service):
        encrypted = enc_service.encrypt_string("api-key-secret-123")
        decrypted = enc_service.decrypt_string(encrypted)
        assert decrypted == "api-key-secret-123"

    def test_json_roundtrip(self, enc_service):
        data = {"provider": "openai", "key": "sk-test-123"}
        encrypted = enc_service.encrypt_json(data)
        decrypted = enc_service.decrypt_json(encrypted)
        assert decrypted == data

    def test_unencrypted_legacy_value_returned_as_is(self, enc_service):
        """Backward compat: if value looks like plain JSON, return it directly."""
        plain_json = '{"provider": "openai"}'
        result = enc_service.decrypt_json(plain_json)
        assert result == {"provider": "openai"}

    def test_none_value_handled(self, enc_service):
        assert enc_service.encrypt_json(None) is None
        assert enc_service.decrypt_json("") is None
        assert enc_service.decrypt_json(None) is None

    def test_no_key_passes_through(self, no_key_service):
        """Without encryption key, strings pass through unencrypted."""
        result = no_key_service.encrypt_string("plaintext")
        assert result == "plaintext"
        result = no_key_service.decrypt_string("plaintext")
        assert result == "plaintext"
