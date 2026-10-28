"""Pool tokens for the HF mirror: stateless, signed with the internal key."""

import pytest

from common import mirror_token

KEY = "k" * 32


def test_a_token_names_its_pool():
    assert mirror_token.verify(mirror_token.make("pool-1", KEY), KEY) == "pool-1"


def test_a_pool_id_with_a_colon_round_trips():
    assert mirror_token.verify(mirror_token.make("a:b", KEY), KEY) == "a:b"


@pytest.mark.parametrize("token", [
    "", None, "pool-1", ":abc", "pool-1:", "pool-1:" + "0" * 64,
])
def test_malformed_or_unsigned_tokens_are_refused(token):
    assert mirror_token.verify(token, KEY) is None


def test_a_token_for_one_pool_can_not_be_moved_to_another():
    sig = mirror_token.make("pool-1", KEY).rpartition(":")[2]
    assert mirror_token.verify(f"pool-2:{sig}", KEY) is None


def test_another_installs_token_is_refused():
    assert mirror_token.verify(mirror_token.make("pool-1", "x" * 32), KEY) is None


def test_no_key_refuses_everything():
    assert mirror_token.verify(mirror_token.make("pool-1", ""), "") is None
