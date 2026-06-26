"""Unit tests for scripts/setup/inferia_setup.py (the setup.sh logic helper).

The helper lives outside ``src`` (it ships next to ``setup.sh``), so it is loaded
by file path rather than imported as a package.
"""
import base64
import subprocess
import sys
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parents[3] / "scripts" / "setup" / "inferia_setup.py"
# Import as a named module (not by file path) so coverage --cov=inferia_setup attaches.
sys.path.insert(0, str(_HELPER.parent))
import inferia_setup as mod  # noqa: E402


# --------------------------------------------------------------------------- #
# secret generation
# --------------------------------------------------------------------------- #
def test_gen_fernet_key_is_valid_fernet():
    from cryptography.fernet import Fernet

    key = mod.gen_fernet_key()
    f = Fernet(key.encode())  # must not raise
    assert f.decrypt(f.encrypt(b"x")) == b"x"
    assert len(base64.urlsafe_b64decode(key)) == 32


def test_gen_fernet_key_non_deterministic():
    assert mod.gen_fernet_key() != mod.gen_fernet_key()


def test_gen_hex_length_and_charset():
    h = mod.gen_hex(32)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_gen_password_is_valid_and_safe():
    for _ in range(20):
        pw = mod.gen_password()
        assert mod.validate_password(pw) == pw
        assert 8 <= len(pw.encode()) <= 72
    assert mod.gen_password() != mod.gen_password()


def test_gen_password_custom_length():
    assert len(mod.gen_password(32)) == 32


# --------------------------------------------------------------------------- #
# password validation — the bcrypt 72-byte overflow edge case
# --------------------------------------------------------------------------- #
def test_validate_password_overflow_rejected():
    with pytest.raises(mod.SetupError):
        mod.validate_password("a" * 73)


def test_validate_password_72_bytes_ok():
    assert mod.validate_password("a" * 72) == "a" * 72


def test_validate_password_multibyte_overflow_rejected():
    # 37 * 2 bytes = 74 bytes > 72 even though only 37 chars
    with pytest.raises(mod.SetupError):
        mod.validate_password("é" * 37)


def test_validate_password_too_short_rejected():
    with pytest.raises(mod.SetupError):
        mod.validate_password("short")


def test_validate_password_min_boundary_ok():
    assert mod.validate_password("12345678") == "12345678"


@pytest.mark.parametrize("bad", ["pass$word", 'pa"ss', "pa'ss", "pa`ss", "pass word", "tab\tval"])
def test_validate_password_forbidden_chars(bad):
    with pytest.raises(mod.SetupError):
        mod.validate_password(bad + "12345678")


# --------------------------------------------------------------------------- #
# email validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("good", ["a@b.co", "admin@inferia.local", "x.y+z@sub.domain.io"])
def test_validate_email_ok(good):
    assert mod.validate_email(good) == good


@pytest.mark.parametrize("bad", ["nope", "a@b", "a@@b.co", "a b@c.co", "@b.co", "a@b.co "])
def test_validate_email_bad(bad):
    with pytest.raises(mod.SetupError):
        mod.validate_email(bad)


# --------------------------------------------------------------------------- #
# URL parsing + derivation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,origin", [
    ("https://h.io", "https://h.io"),
    ("https://h.io/", "https://h.io"),
    ("http://h:8000/x/y", "http://h:8000"),
    ("https://Sub.Host.io/path/", "https://Sub.Host.io"),
])
def test_parse_origin_ok(url, origin):
    assert mod.parse_origin(url) == origin


@pytest.mark.parametrize("bad", ["h.io", "ftp://h.io", "https://", "", "   "])
def test_parse_origin_bad(bad):
    with pytest.raises(mod.SetupError):
        mod.parse_origin(bad)


def test_derive_urls():
    u = mod.derive_urls("https://h.io")
    assert u["DASHBOARD_API_GATEWAY_URL"] == "https://h.io/api"
    assert u["DASHBOARD_INFERENCE_URL"] == "https://h.io/inf"
    assert u["OAUTH_REDIRECT_URI"] == "https://h.io/auth/callback"
    assert u["INFERIA_CONTROL_PLANE_EXTERNAL_URL"] == "https://h.io/api"
    assert u["INFERIA_MODEL_MIRROR_BASE"] == "https://h.io/api"
    assert "https://h.io" in u["ALLOWED_ORIGINS"]
    assert "http://localhost:3001" in u["ALLOWED_ORIGINS"]


# --------------------------------------------------------------------------- #
# build_env
# --------------------------------------------------------------------------- #
_SECRETS = {"JWT_SECRET_KEY": "j", "INTERNAL_API_KEY": "i",
            "SECRET_ENCRYPTION_KEY": "s", "LOG_ENCRYPTION_KEY": "l"}


def test_build_env_dsn_is_bare_and_consistent():
    env = mod.build_env(origin="https://h.io", app_port=8000, auth_mode="local",
                        email="a@b.co", password="password123", pg_password="pgpw123",
                        secrets_map=_SECRETS)
    assert env["DATABASE_URL"] == "postgresql://inferia:pgpw123@postgres:5432/inferia"
    assert "+asyncpg" not in env["DATABASE_URL"]
    assert env["POSTGRES_PASSWORD"] == "pgpw123" == env["PG_ADMIN_PASSWORD"]
    assert env["AUTH_PROVIDER"] == "local"
    assert env["SUPERADMIN_EMAIL"] == "a@b.co"
    assert env["SUPERADMIN_PASSWORD"] == "password123"
    assert env["APP_PORT"] == "8000"
    assert env["JWT_SECRET_KEY"] == "j"
    assert env["SECRET_ENCRYPTION_KEY"] == "s"


def test_build_env_local_leaves_idp_dormant():
    env = mod.build_env(origin="https://h.io", app_port=8000, auth_mode="local",
                        email="a@b.co", password="password123", pg_password="pgpw1234",
                        secrets_map=_SECRETS)
    # present (so .env is complete) but not populated with an IdP
    assert env["AUTH_PROVIDER"] == "local"
    assert env["OAUTH_CLIENT_ID"] == ""


def test_build_env_sso_requires_idp_fields():
    with pytest.raises(mod.SetupError):
        mod.build_env(origin="https://h.io", app_port=8000, auth_mode="inferiaauth",
                      email="a@b.co", password="password123", pg_password="pg123456",
                      secrets_map=_SECRETS)
    env = mod.build_env(origin="https://h.io", app_port=8000, auth_mode="inferiaauth",
                        email="a@b.co", password="password123", pg_password="pg123456",
                        secrets_map=_SECRETS, external_auth_url="https://idp",
                        oauth_client_id="cid")
    assert env["AUTH_PROVIDER"] == "inferiaauth"
    assert env["EXTERNAL_AUTH_URL"] == "https://idp"
    assert env["EXTERNAL_AUTH_ISSUER"] == "https://idp"
    assert env["OAUTH_CLIENT_ID"] == "cid"
    assert env["OAUTH_REDIRECT_URI"] == "https://h.io/auth/callback"


def test_build_env_bad_auth_mode():
    with pytest.raises(mod.SetupError):
        mod.build_env(origin="https://h.io", app_port=8000, auth_mode="nope",
                      email="a@b.co", password="password123", pg_password="pg123456",
                      secrets_map=_SECRETS)


def test_build_env_worker_tag_and_hf():
    env = mod.build_env(origin="https://h.io", app_port=9000, auth_mode="local",
                        email="a@b.co", password="password123", pg_password="pg123456",
                        secrets_map=_SECRETS, worker_image_tag="0.3.0", hf_token="hf_x")
    assert env["INFERIA_WORKER_IMAGE_TAG"] == "0.3.0"
    assert env["INFERIA_HF_TOKEN"] == "hf_x"
    assert env["APP_PORT"] == "9000"


def test_build_env_invalid_password_propagates():
    with pytest.raises(mod.SetupError):
        mod.build_env(origin="https://h.io", app_port=8000, auth_mode="local",
                      email="a@b.co", password="a" * 73, pg_password="pg123456",
                      secrets_map=_SECRETS)


def test_build_env_invalid_email_propagates():
    with pytest.raises(mod.SetupError):
        mod.build_env(origin="https://h.io", app_port=8000, auth_mode="local",
                      email="bad", password="password123", pg_password="pg123456",
                      secrets_map=_SECRETS)


# --------------------------------------------------------------------------- #
# render / parse / merge
# --------------------------------------------------------------------------- #
def test_render_env_roundtrip_and_quotes():
    text = mod.render_env({"A": "1", "B": "x y"})
    assert 'A="1"' in text and 'B="x y"' in text
    assert mod.parse_env_text(text) == {"A": "1", "B": "x y"}


def test_parse_env_text_handles_comments_and_blanks():
    text = '# comment\n\nA="1"\nB=2\n  # indented\nC="has = sign"\n'
    d = mod.parse_env_text(text)
    assert d == {"A": "1", "B": "2", "C": "has = sign"}


def test_parse_env_text_skips_empty_key():
    assert mod.parse_env_text('="orphan"\nA="1"\n') == {"A": "1"}


def test_render_env_is_complete_against_example():
    """Every key in .env.example must be produced by build_env/render."""
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    example_keys = set(mod.parse_env_text(example).keys())
    env = mod.build_env(origin="https://h.io", app_port=8000, auth_mode="local",
                        email="a@b.co", password="password123", pg_password="pg123456",
                        secrets_map=_SECRETS)
    missing = example_keys - set(env.keys())
    assert not missing, f"build_env missing keys present in .env.example: {missing}"


def test_merge_preserve_keeps_secrets_and_pg():
    existing = {"SECRET_ENCRYPTION_KEY": "OLD_FERNET", "POSTGRES_PASSWORD": "oldpg",
                "PG_ADMIN_PASSWORD": "oldpg", "LOG_ENCRYPTION_KEY": "oldlog",
                "DATABASE_URL": "postgresql://inferia:oldpg@postgres:5432/inferia"}
    fresh = {"SECRET_ENCRYPTION_KEY": "NEW", "POSTGRES_PASSWORD": "newpg",
             "PG_ADMIN_PASSWORD": "newpg", "LOG_ENCRYPTION_KEY": "newlog",
             "DATABASE_URL": "postgresql://inferia:newpg@postgres:5432/inferia",
             "JWT_SECRET_KEY": "newjwt"}
    merged = mod.merge_preserve(existing, fresh)
    assert merged["SECRET_ENCRYPTION_KEY"] == "OLD_FERNET"
    assert merged["POSTGRES_PASSWORD"] == "oldpg"
    assert merged["PG_ADMIN_PASSWORD"] == "oldpg"
    assert merged["LOG_ENCRYPTION_KEY"] == "oldlog"
    assert merged["DATABASE_URL"].endswith("oldpg@postgres:5432/inferia")
    assert merged["JWT_SECRET_KEY"] == "newjwt"  # non-preserved is rotated


def test_merge_preserve_no_existing_keys_is_noop():
    fresh = {"JWT_SECRET_KEY": "j", "SECRET_ENCRYPTION_KEY": "new"}
    merged = mod.merge_preserve({}, fresh)
    assert merged == fresh


# --------------------------------------------------------------------------- #
# CLI (generate-env)
# --------------------------------------------------------------------------- #
def _run(args):
    return subprocess.run([sys.executable, str(_HELPER)] + args,
                          capture_output=True, text=True)


def test_cli_generate_env_local_emits_env():
    r = _run(["generate-env", "--public-url", "https://h.io",
              "--email", "admin@h.io", "--password", "password123"])
    assert r.returncode == 0, r.stderr
    env = mod.parse_env_text(r.stdout)
    assert env["AUTH_PROVIDER"] == "local"
    assert env["DATABASE_URL"].startswith("postgresql://")
    from cryptography.fernet import Fernet
    Fernet(env["SECRET_ENCRYPTION_KEY"].encode())  # valid


def test_cli_generates_password_to_stderr():
    r = _run(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io"])
    assert r.returncode == 0, r.stderr
    assert "GENERATED_SUPERADMIN_PASSWORD=" in r.stderr
    env = mod.parse_env_text(r.stdout)
    assert len(env["SUPERADMIN_PASSWORD"]) >= 8


def test_cli_bad_password_exits_2():
    r = _run(["generate-env", "--public-url", "https://h.io",
              "--email", "a@h.io", "--password", "a" * 73])
    assert r.returncode == 2 and "72" in (r.stderr + r.stdout)


def test_cli_bad_url_exits_2():
    r = _run(["generate-env", "--public-url", "not-a-url",
              "--email", "a@h.io", "--password", "password123"])
    assert r.returncode == 2


def test_cli_merge_preserves(tmp_path):
    p = tmp_path / "old.env"
    p.write_text('SECRET_ENCRYPTION_KEY="OLDKEY"\nPOSTGRES_PASSWORD="oldpg"\n'
                 'PG_ADMIN_PASSWORD="oldpg"\n'
                 'DATABASE_URL="postgresql://inferia:oldpg@postgres:5432/inferia"\n')
    r = _run(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io",
              "--password", "password123", "--merge", str(p)])
    assert r.returncode == 0, r.stderr
    env = mod.parse_env_text(r.stdout)
    assert env["SECRET_ENCRYPTION_KEY"] == "OLDKEY"
    assert env["POSTGRES_PASSWORD"] == "oldpg"


def test_cli_merge_missing_file_is_ignored():
    r = _run(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io",
              "--password", "password123", "--merge", "/nonexistent/old.env"])
    assert r.returncode == 0, r.stderr


def test_cli_no_subcommand_exits_nonzero():
    r = _run([])
    assert r.returncode != 0


# --------------------------------------------------------------------------- #
# CLI in-process (for coverage of main/_cmd_generate_env)
# --------------------------------------------------------------------------- #
def test_main_local_inprocess(capsys):
    rc = mod.main(["generate-env", "--public-url", "https://h.io",
                   "--email", "a@h.io", "--password", "password123"])
    assert rc == 0
    out = capsys.readouterr().out
    env = mod.parse_env_text(out)
    assert env["AUTH_PROVIDER"] == "local"


def test_main_generates_password_inprocess(capsys):
    rc = mod.main(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "GENERATED_SUPERADMIN_PASSWORD=" in cap.err


def test_main_sso_inprocess(capsys):
    rc = mod.main(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io",
                   "--password", "password123", "--auth-mode", "inferiaauth",
                   "--external-auth-url", "https://idp", "--oauth-client-id", "cid"])
    assert rc == 0
    env = mod.parse_env_text(capsys.readouterr().out)
    assert env["AUTH_PROVIDER"] == "inferiaauth" and env["OAUTH_CLIENT_ID"] == "cid"


def test_main_bad_password_returns_2(capsys):
    rc = mod.main(["generate-env", "--public-url", "https://h.io",
                   "--email", "a@h.io", "--password", "a" * 73])
    assert rc == 2
    assert "72" in capsys.readouterr().err


def test_main_bad_url_returns_2(capsys):
    rc = mod.main(["generate-env", "--public-url", "nope",
                   "--email", "a@h.io", "--password", "password123"])
    assert rc == 2


def test_main_merge_inprocess(tmp_path, capsys):
    p = tmp_path / "old.env"
    p.write_text('SECRET_ENCRYPTION_KEY="OLDKEY"\n')
    rc = mod.main(["generate-env", "--public-url", "https://h.io", "--email", "a@h.io",
                   "--password", "password123", "--merge", str(p)])
    assert rc == 0
    env = mod.parse_env_text(capsys.readouterr().out)
    assert env["SECRET_ENCRYPTION_KEY"] == "OLDKEY"


def test_main_no_subcommand_raises_systemexit():
    with pytest.raises(SystemExit):
        mod.main([])
