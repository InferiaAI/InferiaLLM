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
