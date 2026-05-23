"""Tests for _is_masked / _preserve_masked_secrets in management/configuration.

These guard the providers POST against the dashboard round-trip bug where a
form pre-populated with masked values would be saved as-is, replacing the
real credential in the DB with the literal mask string.
"""
from inferia.services.api_gateway.management.configuration import (
    _is_masked,
    _preserve_masked_secrets,
)


# ---------- _is_masked --------------------------------------------------


def test_is_masked_full_mask():
    assert _is_masked("********") is True


def test_is_masked_partial_pattern():
    # 4 + "..." + 4 == 11 chars total
    assert _is_masked("AKIA...XYZ8") is True


def test_is_masked_empty_or_none():
    assert _is_masked(None) is False
    assert _is_masked("") is False


def test_is_masked_real_aws_key():
    # Real access-key id (20 chars, all caps+digits) — must not look masked
    assert _is_masked("AKIATESTFAKE1234XYZ8") is False


def test_is_masked_real_secret():
    assert _is_masked("real-aws-secret-access-key-not-mask") is False


def test_is_masked_partial_with_asterisk_rejected():
    # A real key that happens to be 11 chars long but contains "*" should
    # NOT be classified as masked (defensive).
    assert _is_masked("abc*...*xyz") is False


# ---------- _preserve_masked_secrets ------------------------------------


def _existing(aws_access="REAL-ACCESS-KEY", aws_secret="real-secret"):
    return {
        "providers": {
            "cloud": {
                "aws": {
                    "access_key_id": aws_access,
                    "secret_access_key": aws_secret,
                    "region": "us-east-1",
                }
            }
        }
    }


def test_preserve_aws_masked_access_key():
    incoming = {"cloud": {"aws": {"access_key_id": "REAL...-KEY", "region": "us-east-1"}}}
    out = _preserve_masked_secrets(incoming, _existing())
    assert out["cloud"]["aws"]["access_key_id"] == "REAL-ACCESS-KEY"


def test_preserve_aws_masked_secret():
    incoming = {"cloud": {"aws": {"secret_access_key": "********", "region": "us-east-1"}}}
    out = _preserve_masked_secrets(incoming, _existing())
    assert out["cloud"]["aws"]["secret_access_key"] == "real-secret"


def test_real_values_pass_through_unchanged():
    incoming = {
        "cloud": {
            "aws": {
                "access_key_id": "AKIAREALKEY1234XYZ8",
                "secret_access_key": "actual-new-secret-key",
                "region": "us-west-2",
            }
        }
    }
    out = _preserve_masked_secrets(incoming, _existing())
    assert out["cloud"]["aws"]["access_key_id"] == "AKIAREALKEY1234XYZ8"
    assert out["cloud"]["aws"]["secret_access_key"] == "actual-new-secret-key"
    assert out["cloud"]["aws"]["region"] == "us-west-2"


def test_masked_with_no_prior_value_drops_field():
    """If the user sends a masked value but the DB has nothing, drop it."""
    incoming = {"cloud": {"aws": {"access_key_id": "********"}}}
    out = _preserve_masked_secrets(incoming, {"providers": {"cloud": {"aws": {}}}})
    assert "access_key_id" not in out["cloud"]["aws"]


def test_input_is_not_mutated():
    incoming = {"cloud": {"aws": {"access_key_id": "REAL...-KEY"}}}
    original = {"cloud": {"aws": {"access_key_id": "REAL...-KEY"}}}
    _preserve_masked_secrets(incoming, _existing())
    assert incoming == original


def test_gcp_service_account_json_preserved_when_masked():
    incoming = {"cloud": {"gcp": {"service_account_json": "********"}}}
    existing = {"providers": {"cloud": {"gcp": {"service_account_json": "{json:real}"}}}}
    out = _preserve_masked_secrets(incoming, existing)
    assert out["cloud"]["gcp"]["service_account_json"] == "{json:real}"


def test_azure_client_secret_preserved_when_masked():
    incoming = {"cloud": {"azure": {"client_secret": "********"}}}
    existing = {"providers": {"cloud": {"azure": {"client_secret": "real-azure"}}}}
    out = _preserve_masked_secrets(incoming, existing)
    assert out["cloud"]["azure"]["client_secret"] == "real-azure"


def test_ibm_api_key_preserved_when_masked():
    incoming = {"cloud": {"ibm": {"api_key": "abcd...wxyz"}}}
    existing = {"providers": {"cloud": {"ibm": {"api_key": "abcdrealwxyz"}}}}
    out = _preserve_masked_secrets(incoming, existing)
    assert out["cloud"]["ibm"]["api_key"] == "abcdrealwxyz"


def test_chroma_api_key_preserved_when_masked():
    incoming = {"vectordb": {"chroma": {"api_key": "********"}}}
    existing = {"providers": {"vectordb": {"chroma": {"api_key": "real-chroma"}}}}
    out = _preserve_masked_secrets(incoming, existing)
    assert out["vectordb"]["chroma"]["api_key"] == "real-chroma"


def test_nosana_wallet_preserved_when_masked():
    incoming = {"depin": {"nosana": {"wallet_private_key": "********"}}}
    existing = {"providers": {"depin": {"nosana": {"wallet_private_key": "real-wallet"}}}}
    out = _preserve_masked_secrets(incoming, existing)
    assert out["depin"]["nosana"]["wallet_private_key"] == "real-wallet"


def test_empty_existing_handled_gracefully():
    incoming = {"cloud": {"aws": {"region": "us-east-1"}}}
    out = _preserve_masked_secrets(incoming, {})
    # No masked fields → no transformation.
    assert out == {"cloud": {"aws": {"region": "us-east-1"}}}
