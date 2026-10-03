"""A daily quota must be counted against whatever owns the limit.

The limit comes from an org-wide policy — the query says so:

    (DBPolicy.org_id == org_id) & (DBPolicy.deployment_id.is_(None))

but the Redis counters were keyed on `user_id`, which is `apikey:<id>` for an
API-key request. So each key got the whole org's allowance to itself, and an
org with five keys could spend five times its limit.

These assert on the keys rather than on behaviour through Redis, because the
keys are where the two sides have to agree: a limit counted against a different
key than it is enforced on is not a limit.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_gateway.policy.engine import PolicyEngine


def _engine(org_for_key=None):
    engine = PolicyEngine.__new__(PolicyEngine)
    import asyncio

    engine._cache_lock = asyncio.Lock()
    engine.org_id_cache = {}
    engine.quota_policy_cache = {}
    engine.redis = AsyncMock()
    if org_for_key:
        engine.org_id_cache.update(org_for_key)
    return engine


def _db(org_id=None):
    """A session whose first query answers the org lookup and whose later ones
    find no quota policy, so the defaults apply."""
    answers = [org_id, None, None, None]

    def _result(*_a, **_k):
        r = MagicMock()
        r.scalars.return_value.first.return_value = (
            answers.pop(0) if answers else None
        )
        return r

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_result)
    return db


class TestTheKeysThemselves:

    def test_a_scope_produces_both_counters(self):
        req, tok = PolicyEngine._quota_keys("org:acme", "2026-10-03", "m")
        assert req == "usage:org:acme:2026-10-03:m:requests"
        assert tok == "usage:org:acme:2026-10-03:m:tokens"

    def test_two_scopes_never_collide(self):
        a = PolicyEngine._quota_keys("org:acme", "2026-10-03", "m")
        b = PolicyEngine._quota_keys("org:other", "2026-10-03", "m")
        assert set(a).isdisjoint(b)


@pytest.mark.asyncio
class TestTheScope:

    async def test_an_api_key_is_counted_against_its_org(self):
        engine = _engine()
        scope = await engine._quota_scope(_db("acme"), "apikey:k1")
        assert scope == "org:acme"

    async def test_every_key_in_one_org_shares_a_scope(self):
        """The bug: five keys used to mean five times the limit."""
        engine = _engine()
        first = await engine._quota_scope(_db("acme"), "apikey:k1")
        second = await engine._quota_scope(_db("acme"), "apikey:k2")
        assert first == second == "org:acme"

    async def test_different_orgs_stay_separate(self):
        engine = _engine()
        acme = await engine._quota_scope(_db("acme"), "apikey:k1")
        other = await engine._quota_scope(_db("other"), "apikey:k2")
        assert acme != other

    async def test_a_key_with_no_org_falls_back_to_itself(self):
        """Narrower than an org, so it cannot spend someone else's quota."""
        engine = _engine()
        scope = await engine._quota_scope(_db(None), "apikey:orphan")
        assert scope == "apikey:orphan"

    async def test_a_sandbox_identity_is_not_treated_as_a_key(self):
        engine = _engine()
        db = _db("acme")
        scope = await engine._quota_scope(db, "sandbox:acme:user-1")
        assert scope == "sandbox:acme:user-1"
        db.execute.assert_not_awaited()

    async def test_a_malformed_identity_does_not_raise(self):
        """An empty key id is not looked up at all."""
        engine = _engine()
        db = _db("acme")
        assert await engine._quota_scope(db, "apikey:") == "apikey:"
        db.execute.assert_not_awaited()

    async def test_the_org_lookup_is_cached(self):
        """The check and the increment arrive as separate requests."""
        engine = _engine()
        db = _db("acme")
        await engine._quota_scope(db, "apikey:k1")
        await engine._quota_scope(db, "apikey:k1")
        assert db.execute.await_count == 1


@pytest.mark.asyncio
class TestBothSidesAgree:
    """The read and the write must land on the same key or nothing binds."""

    async def test_check_and_increment_use_one_key(self):
        engine = _engine()
        engine._check_redis_quota = AsyncMock(return_value=(0, 0))
        engine._increment_redis_with_breaker = AsyncMock()

        # policy lookup finds nothing, so defaults apply and nothing raises
        db = _db("acme")
        await engine.check_quota(db, "apikey:k1", "m")
        read_key = engine._check_redis_quota.await_args.args[0]

        await engine.increment_redis_only(db, "apikey:k1", "m", {"total_tokens": 1})
        write_key = engine._increment_redis_with_breaker.await_args.args[0]

        assert read_key == write_key
        assert "org:acme" in read_key

    async def test_two_keys_in_an_org_increment_the_same_counter(self):
        engine = _engine()
        engine._increment_redis_with_breaker = AsyncMock()

        # A session per call, as each request has its own.
        await engine.increment_redis_only(
            _db("acme"), "apikey:k1", "m", {"total_tokens": 1}
        )
        first = engine._increment_redis_with_breaker.await_args.args[0]

        await engine.increment_redis_only(
            _db("acme"), "apikey:k2", "m", {"total_tokens": 1}
        )
        second = engine._increment_redis_with_breaker.await_args.args[0]

        assert first == second, "each key still has its own counter"
