"""Dry-run smoke for setup.sh — exercises the no-Docker path (--no-up).

These never invoke Docker and write only to a throwaway --env-file, so they
cannot touch the repo's real .env.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"


def _run(args, cwd):
    return subprocess.run(["bash", str(SETUP), *args],
                          capture_output=True, text=True, cwd=str(cwd))


def test_help_runs(tmp_path):
    r = _run(["--help"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "setup.sh" in r.stdout
    assert "--build" in r.stdout and "--force" in r.stdout


def test_no_up_generates_env(tmp_path):
    envf = tmp_path / "gen.env"
    r = _run(["--no-up", "--yes", "--public-url", "https://h.io",
              "--superadmin-email", "admin@h.io",
              "--superadmin-password", "password123",
              "--env-file", str(envf)], tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    text = envf.read_text()
    assert 'AUTH_PROVIDER="local"' in text
    assert 'DATABASE_URL="postgresql://inferia:' in text
    assert "+asyncpg" not in text
    # never the repo's real .env
    assert envf.resolve() != (REPO / ".env").resolve()


def test_no_up_generates_password_when_omitted(tmp_path):
    envf = tmp_path / "gen.env"
    r = _run(["--no-up", "--yes", "--public-url", "https://h.io",
              "--superadmin-email", "admin@h.io", "--env-file", str(envf)], tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    # generated password surfaced to the user once
    assert "password" in (r.stdout + r.stderr).lower()
    assert 'SUPERADMIN_PASSWORD="' in envf.read_text()


def test_existing_env_not_overwritten_without_force(tmp_path):
    envf = tmp_path / "gen.env"
    envf.write_text('SENTINEL="keep-me"\n')
    r = _run(["--no-up", "--yes", "--public-url", "https://h.io",
              "--superadmin-email", "admin@h.io",
              "--superadmin-password", "password123",
              "--env-file", str(envf)], tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "keep-me" in envf.read_text()  # untouched


def test_force_regenerates_but_preserves_secret_key(tmp_path):
    envf = tmp_path / "gen.env"
    envf.write_text('SECRET_ENCRYPTION_KEY="OLDKEY"\nPOSTGRES_PASSWORD="oldpg"\n'
                    'PG_ADMIN_PASSWORD="oldpg"\n'
                    'DATABASE_URL="postgresql://inferia:oldpg@postgres:5432/inferia"\n')
    r = _run(["--no-up", "--yes", "--force", "--public-url", "https://h.io",
              "--superadmin-email", "admin@h.io",
              "--superadmin-password", "password123",
              "--env-file", str(envf)], tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    text = envf.read_text()
    assert 'SECRET_ENCRYPTION_KEY="OLDKEY"' in text  # preserved across --force
    assert 'POSTGRES_PASSWORD="oldpg"' in text


def test_bad_url_fails(tmp_path):
    envf = tmp_path / "gen.env"
    r = _run(["--no-up", "--yes", "--public-url", "nope",
              "--superadmin-email", "admin@h.io",
              "--superadmin-password", "password123",
              "--env-file", str(envf)], tmp_path)
    assert r.returncode != 0


def test_yes_without_public_url_fails(tmp_path):
    r = _run(["--no-up", "--yes", "--superadmin-email", "admin@h.io",
              "--env-file", str(tmp_path / "x.env")], tmp_path)
    assert r.returncode != 0


# --- load_env_settings: recover verification targets from an existing .env --- #
def _eval_load(env_text, tmp_path, *, public="", app_port="8000", explicit=0):
    """Source setup.sh, run load_env_settings against a temp .env, echo results."""
    envf = tmp_path / "existing.env"
    envf.write_text(env_text)
    snippet = (
        f'source "{SETUP}"; '
        f'ENV_FILE="{envf}"; PUBLIC_URL="{public}"; APP_PORT="{app_port}"; '
        f'APP_PORT_EXPLICIT={explicit}; SUPERADMIN_EMAIL=""; '
        f'load_env_settings; '
        f'printf "%s|%s|%s\\n" "$PUBLIC_URL" "$APP_PORT" "$SUPERADMIN_EMAIL"'
    )
    r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def test_load_env_derives_public_url_from_gateway(tmp_path):
    out = _eval_load(
        'APP_PORT="9000"\n'
        'DASHBOARD_API_GATEWAY_URL="https://my.host/api"\n'
        'SUPERADMIN_EMAIL="root@my.host"\n', tmp_path)
    assert out == "https://my.host|9000|root@my.host"


def test_load_env_falls_back_to_allowed_origins(tmp_path):
    # same-origin "/api" carries no host -> use last absolute ALLOWED_ORIGINS entry
    out = _eval_load(
        'DASHBOARD_API_GATEWAY_URL="/api"\n'
        'ALLOWED_ORIGINS="http://localhost:3001,https://pub.example.com"\n', tmp_path)
    assert out.startswith("https://pub.example.com|")


def test_load_env_explicit_flags_win(tmp_path):
    # an explicitly-provided public URL + app port are NOT overwritten by .env
    out = _eval_load(
        'APP_PORT="9000"\nDASHBOARD_API_GATEWAY_URL="https://from.env/api"\n',
        tmp_path, public="https://from.flag", app_port="7000", explicit=1)
    assert out == "https://from.flag|7000|"


def test_load_env_noop_when_file_absent(tmp_path):
    snippet = (
        f'source "{SETUP}"; ENV_FILE="{tmp_path/"nope.env"}"; '
        f'PUBLIC_URL=""; APP_PORT="8000"; APP_PORT_EXPLICIT=0; SUPERADMIN_EMAIL=""; '
        f'load_env_settings; printf "%s|%s\\n" "$PUBLIC_URL" "$APP_PORT"'
    )
    r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "|8000"
