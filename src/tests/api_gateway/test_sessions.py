"""Ending sessions: logout, password change and removal really cut access.

Every token carries the user's token_version as "tv"; bumping it ends every
token issued before. Tokens from before this existed carry none and count as 0.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest
from fastapi import HTTPException
from jose import jwt

from api_gateway.db.models import User as DBUser
from api_gateway.models import UserContext
from api_gateway.rbac import middleware as mw
from api_gateway.rbac import router as r
from api_gateway.rbac.auth import auth_service
from api_gateway.schemas.auth import ChangePasswordRequest, TOTPDisableRequest


def _user(tv=0, **kw):
    u = DBUser(id="u1", email="a@example.com", password_hash=auth_service.get_password_hash("old-pass-1"),
               default_org_id="org1", totp_enabled=False, token_version=tv)
    for k, v in kw.items():
        setattr(u, k, v)
    return u


def _db_returning(user, membership=None):
    """A session whose user lookup returns *user* and membership lookup *membership*."""
    def _result(obj):
        res = MagicMock()
        res.scalars.return_value.first.return_value = obj
        return res

    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = lambda *a, **k: _result(user) if db.execute.await_count == 1 else _result(membership)
    return db


def _ctx(local=True, roles=("admin",)):
    return UserContext(user_id="u1", username="a@example.com", email="a@example.com",
                       roles=list(roles), permissions=[], org_id="org1",
                       quota_limit=1, quota_used=0, local_session=local)


def _request(ctx):
    return SimpleNamespace(state=SimpleNamespace(user=ctx))


class TestTokenVersion:

    def test_tokens_carry_the_users_version(self):
        token = auth_service.create_access_token(_user(tv=3), org_id="org1", role="admin")
        assert jwt.get_unverified_claims(token)["tv"] == 3
        refresh = auth_service.create_refresh_token(_user(tv=3))
        assert jwt.get_unverified_claims(refresh)["tv"] == 3

    @pytest.mark.asyncio
    async def test_a_token_from_before_sessions_ended_is_refused(self):
        token = auth_service.create_access_token(_user(tv=0), org_id="org1", role="admin")
        with pytest.raises(HTTPException) as exc:
            await auth_service.get_current_user(_db_returning(_user(tv=1)), token)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_a_current_token_still_works(self):
        token = auth_service.create_access_token(_user(tv=1), org_id="org1", role="admin")
        member = SimpleNamespace(role="admin")
        user, org, roles = await auth_service.get_current_user(_db_returning(_user(tv=1), member), token)
        assert (org, roles) == ("org1", ["admin"])

    @pytest.mark.asyncio
    async def test_an_old_refresh_token_is_refused(self):
        refresh = auth_service.create_refresh_token(_user(tv=0))
        with pytest.raises(HTTPException) as exc:
            await auth_service.refresh_access_token(refresh, _db_returning(_user(tv=2)))
        assert exc.value.status_code == 401

    def test_tokens_issued_before_tv_existed_count_as_version_0(self):
        claims = {"sub": "u1", "exp": 9999999999, "iat": 1, "type": "access", "org_id": "org1"}
        token = jwt.encode(claims, auth_service.secret_key, algorithm=auth_service.algorithm)
        assert auth_service.decode_token(token).tv == 0


@pytest.mark.asyncio
async def test_ending_sessions_bumps_the_version_and_drops_cached_logins():
    user = _user(tv=4)
    mw._auth_cache["tok-a"] = _ctx()
    mw._auth_cache["tok-other"] = UserContext(
        user_id="someone-else", username="b", email="b", roles=[], permissions=[],
        quota_limit=1, quota_used=0)
    await auth_service.end_sessions(AsyncMock(), user)
    assert user.token_version == 5
    assert "tok-a" not in mw._auth_cache
    assert "tok-other" in mw._auth_cache
    mw._auth_cache.clear()


class TestLogout:

    @pytest.mark.asyncio
    async def test_a_local_logout_ends_every_session(self):
        user = _user(tv=0)
        with patch.object(auth_service, "end_sessions", AsyncMock()) as end:
            await r.logout(_request(_ctx(local=True)), _db_returning(user))
        end.assert_awaited_once_with(end.await_args.args[0], user)

    @pytest.mark.asyncio
    async def test_an_sso_logout_leaves_local_state_alone(self):
        with patch.object(auth_service, "end_sessions", AsyncMock()) as end:
            await r.logout(_request(_ctx(local=False)), AsyncMock())
        end.assert_not_awaited()


class TestChangePassword:

    @pytest.mark.asyncio
    async def test_the_current_password_must_be_right(self):
        with pytest.raises(HTTPException) as exc:
            await r.change_password(
                ChangePasswordRequest(current_password="wrong", new_password="new-pass-1"),
                _request(_ctx()), _db_returning(_user()))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_the_new_password_must_differ(self):
        with pytest.raises(HTTPException):
            await r.change_password(
                ChangePasswordRequest(current_password="old-pass-1", new_password="old-pass-1"),
                _request(_ctx()), _db_returning(_user()))

    def test_a_short_password_is_rejected(self):
        with pytest.raises(ValueError):
            ChangePasswordRequest(current_password="x", new_password="short")

    @pytest.mark.asyncio
    async def test_it_ends_other_sessions_and_keeps_this_one(self):
        user = _user(tv=2)
        with patch.object(r.audit_service, "log_event", AsyncMock()):
            out = await r.change_password(
                ChangePasswordRequest(current_password="old-pass-1", new_password="new-pass-1"),
                _request(_ctx()), _db_returning(user))
        assert auth_service.verify_password("new-pass-1", user.password_hash)
        assert user.token_version == 3
        # The returned tokens belong to the new version, so this browser stays in.
        assert jwt.get_unverified_claims(out.access_token)["tv"] == 3
        assert jwt.get_unverified_claims(out.refresh_token)["tv"] == 3


class TestDisable2FA:

    def _2fa_user(self):
        secret = pyotp.random_base32()
        return _user(totp_enabled=True, totp_secret=secret), secret

    @pytest.mark.asyncio
    async def test_a_session_alone_cannot_turn_2fa_off(self):
        user, secret = self._2fa_user()
        with pytest.raises(HTTPException) as exc:
            await r.totp_disable(TOTPDisableRequest(password="wrong", totp_code=pyotp.TOTP(secret).now()),
                                 _request(_ctx()), _db_returning(user))
        assert exc.value.status_code == 400
        assert user.totp_enabled

    @pytest.mark.asyncio
    async def test_a_wrong_code_is_refused(self):
        user, _ = self._2fa_user()
        with pytest.raises(HTTPException):
            await r.totp_disable(TOTPDisableRequest(password="old-pass-1", totp_code="000000"),
                                 _request(_ctx()), _db_returning(user))
        assert user.totp_enabled

    @pytest.mark.asyncio
    async def test_password_and_code_turn_it_off(self):
        user, secret = self._2fa_user()
        # bcrypt takes ~0.3s, enough to cross a 30s code boundary now and then.
        with (
            patch.object(r.audit_service, "log_event", AsyncMock()),
            patch.object(r.auth_service, "verify_password", return_value=True),
        ):
            await r.totp_disable(TOTPDisableRequest(password="old-pass-1", totp_code=pyotp.TOTP(secret).now()),
                                 _request(_ctx()), _db_returning(user))
        assert not user.totp_enabled


class TestRemoveMember:

    @staticmethod
    def _db(membership, member):
        results = [membership, member]

        def _next(*a, **k):
            res = MagicMock()
            res.scalars.return_value.first.return_value = results.pop(0) if results else None
            return res

        db = AsyncMock()
        db.execute.side_effect = _next
        return db

    @pytest.mark.asyncio
    async def test_you_cannot_remove_yourself(self):
        from api_gateway.rbac import users_router as ur

        with patch.object(ur.authz_service, "require_permission"):
            with pytest.raises(HTTPException) as exc:
                await ur.remove_member("u1", _request(_ctx()), AsyncMock())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_stronger_member_cannot_be_removed(self):
        from api_gateway.rbac import users_router as ur

        db = self._db(SimpleNamespace(role="admin"), _user(id="u2", email="b@example.com"))
        with (
            patch.object(ur.authz_service, "require_permission"),
            patch.object(ur.authz_service, "require_grantable_role",
                         AsyncMock(side_effect=HTTPException(403, "no"))),
        ):
            with pytest.raises(HTTPException) as exc:
                await ur.remove_member("u2", _request(_ctx()), db)
        assert exc.value.status_code == 403
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_the_superadmin_can_remove_the_superadmin(self, monkeypatch):
        from api_gateway.config import settings
        from api_gateway.rbac import users_router as ur

        monkeypatch.setattr(settings, "superadmin_email", "root@example.com")
        db = self._db(SimpleNamespace(role="admin"), _user(id="u2", email="Root@example.com"))
        with patch.object(ur.authz_service, "require_permission"):
            with pytest.raises(HTTPException) as exc:
                await ur.remove_member("u2", _request(_ctx()), db)
        assert exc.value.status_code == 403
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_removing_a_member_deletes_their_membership(self):
        from api_gateway.rbac import users_router as ur

        membership = SimpleNamespace(role="member")
        member = _user(id="u2", email="b@example.com", default_org_id="org1")
        db = self._db(membership, member)
        mw._auth_cache["tok-u2"] = UserContext(
            user_id="u2", username="b", email="b", roles=[], permissions=[],
            quota_limit=1, quota_used=0)
        with (
            patch.object(ur.authz_service, "require_permission"),
            patch.object(ur.authz_service, "require_grantable_role", AsyncMock()),
        ):
            await ur.remove_member("u2", _request(_ctx()), db)
        db.delete.assert_awaited_once_with(membership)
        assert member.default_org_id is None
        assert "tok-u2" not in mw._auth_cache


class TestReplacing2FA:
    """A stolen session must not swap the victim's authenticator for its own."""

    @pytest.mark.asyncio
    async def test_setup_is_refused_while_2fa_is_on(self):
        user = _user(totp_enabled=True, totp_secret=pyotp.random_base32())
        with pytest.raises(HTTPException) as exc:
            await r.totp_setup(_request(_ctx()), _db_returning(user))
        assert exc.value.status_code == 409
        assert user.totp_pending_secret is None

    @pytest.mark.asyncio
    async def test_verify_is_refused_while_2fa_is_on(self):
        pending = pyotp.random_base32()
        user = _user(totp_enabled=True, totp_secret="old", totp_pending_secret=pending)
        with pytest.raises(HTTPException) as exc:
            await r.totp_verify(r.TOTPVerifyRequest(totp_code=pyotp.TOTP(pending).now()),
                                _request(_ctx()), _db_returning(user))
        assert exc.value.status_code == 409
        assert user.totp_secret == "old"


@pytest.mark.asyncio
async def test_wrong_passwords_on_password_change_hit_the_account_cap():
    from common.rate_limit import login_account_limiter

    login_account_limiter._store.clear()
    try:
        codes = []
        for _ in range(22):
            try:
                await r.change_password(
                    ChangePasswordRequest(current_password="wrong", new_password="new-pass-1"),
                    _request(_ctx()), _db_returning(_user()))
            except HTTPException as e:
                codes.append(e.status_code)
        assert codes[:21] == [400] * 21
        assert codes[21] == 429
    finally:
        login_account_limiter._store.clear()
