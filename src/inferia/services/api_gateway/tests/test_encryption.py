"""Tests for AES-256-GCM and Fernet encryption — security layer."""

import os
import base64
import pytest

from inferia.services.api_gateway.security.encryption import LogEncryption


@pytest.fixture
def encryption():
    """LogEncryption with a deterministic test key."""
    # 32 bytes = 64 hex chars
    key_hex = os.urandom(32).hex()
    return LogEncryption(key_hex)


@pytest.fixture
def key_hex():
    return os.urandom(32).hex()


class TestLogEncryptionRoundtrip:
    """AES-256-GCM encrypt/decrypt roundtrip."""

    def test_string_roundtrip(self, encryption):
        plaintext = "Hello, InferiaLLM!"
        ciphertext = encryption.encrypt(plaintext)
        decrypted = encryption.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_dict_roundtrip(self, encryption):
        data = {"user": "test", "action": "deploy", "count": 42}
        ciphertext = encryption.encrypt(data)
        decrypted = encryption.decrypt(ciphertext)
        assert decrypted == data

    def test_unique_nonce_per_encryption(self, encryption):
        """Two encryptions of same plaintext produce different ciphertexts."""
        plaintext = "same text"
        c1 = encryption.encrypt(plaintext)
        c2 = encryption.encrypt(plaintext)
        assert c1 != c2

    def test_empty_string_roundtrip(self, encryption):
        ciphertext = encryption.encrypt("")
        decrypted = encryption.decrypt(ciphertext)
        assert decrypted == ""

    def test_large_payload_roundtrip(self, encryption):
        plaintext = "A" * 10240  # 10KB
        ciphertext = encryption.encrypt(plaintext)
        decrypted = encryption.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_base64_encoding_valid(self, encryption):
        ciphertext = encryption.encrypt("test")
        # Should be valid base64 — no exception on decode
        raw = base64.b64decode(ciphertext)
        assert len(raw) > 12  # nonce (12 bytes) + at least some ciphertext


class TestLogEncryptionTampering:
    """Tampered/invalid ciphertext must be rejected."""

    def test_tampered_ciphertext_raises(self, encryption):
        ciphertext = encryption.encrypt("secret data")
        # Tamper with a byte in the middle
        raw = bytearray(base64.b64decode(ciphertext))
        raw[20] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode("utf-8")
        with pytest.raises(ValueError, match="Decryption failed"):
            encryption.decrypt(tampered)

    def test_truncated_ciphertext_raises(self, encryption):
        ciphertext = encryption.encrypt("secret data")
        truncated = ciphertext[:10]
        with pytest.raises((ValueError, Exception)):
            encryption.decrypt(truncated)

    def test_wrong_key_fails(self, key_hex):
        enc1 = LogEncryption(key_hex)
        different_key = os.urandom(32).hex()
        enc2 = LogEncryption(different_key)

        ciphertext = enc1.encrypt("secret")
        with pytest.raises((ValueError, Exception)):
            enc2.decrypt(ciphertext)
