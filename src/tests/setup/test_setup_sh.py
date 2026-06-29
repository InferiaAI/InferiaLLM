"""Dry-run smoke for setup.sh — exercises the no-Docker path (--no-up).

These never invoke Docker and write only to a throwaway --env-file, so they
cannot touch the repo's real .env.
"""
import os
import stat
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


# --------------------------------------------------------------------------- #
# Docker install: OS detection, family mapping, installer dispatch
# --------------------------------------------------------------------------- #
def _bash(snippet, **env):
    import os
    e = {**os.environ, **env}
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, env=e)


def _os_release(tmp_path, **kv):
    f = tmp_path / "os-release"
    f.write_text("".join(f'{k}={v}\n' for k, v in kv.items()))
    return f


def _detect(tmp_path, **kv):
    osr = _os_release(tmp_path, **kv)
    snippet = (
        f'source "{SETUP}"; OS_RELEASE_FILE="{osr}"; '
        f'detect_os; printf "%s|%s|%s\\n" "$OS_ID" "$OS_LIKE" "$(os_family)"'
    )
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("kv,family", [
    (dict(ID="ubuntu", ID_LIKE="debian"), "debian"),
    (dict(ID="debian"), "debian"),
    (dict(ID="linuxmint", ID_LIKE='"ubuntu debian"'), "debian"),
    (dict(ID="fedora"), "fedora"),
    (dict(ID="centos", ID_LIKE='"rhel fedora"'), "rhel"),
    (dict(ID="rocky", ID_LIKE='"rhel centos fedora"'), "rhel"),
    (dict(ID="arch"), "arch"),
    (dict(ID="manjaro", ID_LIKE="arch"), "arch"),
    (dict(ID="opensuse-leap", ID_LIKE='"suse opensuse"'), "suse"),
    (dict(ID="sles"), "suse"),
])
def test_detect_os_family(tmp_path, kv, family):
    out = _detect(tmp_path, **kv)
    assert out.endswith(f"|{family}")


def test_detect_os_family_unknown_via_id(tmp_path):
    assert _detect(tmp_path, ID="plan9").endswith("|unknown")


def test_detect_os_family_via_id_like_only(tmp_path):
    # unknown ID but a debian-ish ID_LIKE still maps to the debian installer
    assert _detect(tmp_path, ID="customdistro", ID_LIKE="debian").endswith("|debian")


def _dispatch(tmp_path, **kv):
    """Stub every installer to echo its name; assert which install_docker picks."""
    osr = _os_release(tmp_path, **kv)
    stubs = "; ".join(
        f'{fn}() {{ echo "CALLED:{fn}"; return 0; }}'
        for fn in ("install_docker_debian", "install_docker_fedora",
                   "install_docker_rhel", "install_docker_arch",
                   "install_docker_suse", "install_docker_convenience")
    )
    snippet = f'source "{SETUP}"; OS_RELEASE_FILE="{osr}"; {stubs}; install_docker'
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("kv,fn", [
    (dict(ID="ubuntu", ID_LIKE="debian", VERSION_CODENAME="jammy"), "install_docker_debian"),
    (dict(ID="fedora"), "install_docker_fedora"),
    (dict(ID="rocky", ID_LIKE="rhel"), "install_docker_rhel"),
    (dict(ID="arch"), "install_docker_arch"),
    (dict(ID="sles"), "install_docker_suse"),
])
def test_install_docker_dispatch(tmp_path, kv, fn):
    out = _dispatch(tmp_path, **kv)
    assert f"CALLED:{fn}" in out
    assert "CALLED:install_docker_convenience" not in out


def test_install_docker_unknown_uses_convenience(tmp_path):
    out = _dispatch(tmp_path, ID="plan9")
    assert "CALLED:install_docker_convenience" in out


def test_install_docker_falls_back_to_convenience_on_native_failure(tmp_path):
    osr = _os_release(tmp_path, ID="arch")
    snippet = (
        f'source "{SETUP}"; OS_RELEASE_FILE="{osr}"; '
        f'install_docker_arch() {{ return 1; }}; '
        f'install_docker_convenience() {{ echo "CALLED:convenience"; return 0; }}; '
        f'install_docker'
    )
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    assert "CALLED:convenience" in r.stdout


def _fake_docker_bin(tmp_path, *, info_ok=True, compose_ok=True):
    """Create a fake `docker` on PATH that fakes `info` / `compose version`."""
    binp = tmp_path / "bin"
    binp.mkdir(exist_ok=True)
    d = binp / "docker"
    d.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        f"  info) exit {0 if info_ok else 1} ;;\n"
        f"  compose) [ \"$2\" = version ] && exit {0 if compose_ok else 1}; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    d.chmod(0o755)
    return binp


def test_ensure_docker_skips_install_when_present(tmp_path):
    binp = _fake_docker_bin(tmp_path)
    snippet = (
        f'source "{SETUP}"; DOCKER=(docker); '
        f'install_docker() {{ echo "INSTALL-CALLED"; }}; '
        f'ensure_docker'
    )
    r = _bash(snippet, PATH=f"{binp}:" + __import__("os").environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert "INSTALL-CALLED" not in (r.stdout + r.stderr)


def test_ensure_docker_no_install_flag_dies_when_missing(tmp_path):
    # Force the "docker missing" branch PATH-independently by shadowing the
    # `command` builtin just for `command -v docker`.
    snippet = (
        f'source "{SETUP}"; NO_INSTALL_DOCKER=1; DOCKER=(docker); '
        f'command() {{ if [ "$1" = -v ] && [ "$2" = docker ]; then return 1; fi; '
        f'builtin command "$@"; }}; '
        f'ensure_docker'
    )
    r = _bash(snippet)
    assert r.returncode != 0
    assert "no-install-docker" in (r.stdout + r.stderr).lower()


# --------------------------------------------------------------------------- #
# ID_LIKE-only family fallback + uname fallback
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("like,family", [
    ("rhel", "rhel"), ("fedora", "rhel"), ("arch", "arch"), ("suse", "suse"),
    ("debian", "debian"),
])
def test_detect_os_family_id_like_only(tmp_path, like, family):
    # unknown ID, family resolved purely from ID_LIKE
    assert _detect(tmp_path, ID="weirddistro", ID_LIKE=like).endswith(f"|{family}")


def test_detect_os_uname_fallback_when_no_os_release(tmp_path):
    missing = tmp_path / "nope-os-release"
    snippet = (f'source "{SETUP}"; OS_RELEASE_FILE="{missing}"; '
               'detect_os; echo "OSID=$OS_ID"')
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    out = r.stdout.lower()
    assert "osid=" in out and "linux" in out  # lowercased `uname -s`


def test_detect_os_fedora_direct_is_fedora_not_rhel(tmp_path):
    # regression guard for the asahi/fedora routing fix
    assert _detect(tmp_path, ID="fedora").endswith("|fedora")
    assert _detect(tmp_path, **{"ID": "fedora-asahi-remix", "ID_LIKE": "fedora"}).endswith("|fedora")


# --------------------------------------------------------------------------- #
# per-OS installer bodies — argv-recording dry runs (no real package installs)
# --------------------------------------------------------------------------- #
def _dryrun(body, tmp_path, osr_kv=None):
    """Source setup.sh with as_root/curl/dpkg/mktemp recorders, then run `body`.

    as_root records its argv to REC; when it is `tee`, the piped stdin (e.g. the
    constructed apt repo line) is captured to TEE so we can assert on it.
    Returns (CompletedProcess, REC text, TEE text).
    """
    rec = tmp_path / "rec.log"
    tee = tmp_path / "tee.data"
    osr_line = ""
    if osr_kv is not None:
        osr = _os_release(tmp_path, **osr_kv)
        osr_line = f'OS_RELEASE_FILE="{osr}"; '
    pre = (
        f'source "{SETUP}"; {osr_line}REC="{rec}"; TEE="{tee}"; '
        'as_root() { if [ "$1" = tee ]; then cat >> "$TEE"; echo "AS_ROOT: tee" >> "$REC"; return 0; fi; '
        'echo "AS_ROOT: $*" >> "$REC"; return 0; }; '
        'curl() { echo "CURL: $*" >> "$REC"; return 0; }; '
        'dpkg() { echo amd64; }; '
        f'mktemp() {{ echo "{tmp_path}/conv.sh"; }}; '
    )
    r = _bash(pre + body)
    return (r,
            rec.read_text() if rec.exists() else "",
            tee.read_text() if tee.exists() else "")


def test_install_debian_builds_ubuntu_repo(tmp_path):
    r, rec, tee = _dryrun("detect_os; install_docker_debian", tmp_path,
                          osr_kv=dict(ID="ubuntu", VERSION_CODENAME="jammy", UBUNTU_CODENAME="jammy"))
    assert r.returncode == 0, r.stderr
    assert "CURL: -fsSL https://download.docker.com/linux/ubuntu/gpg" in rec
    assert ("deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] "
            "https://download.docker.com/linux/ubuntu jammy stable") in tee
    assert ("apt-get install -y docker-ce docker-ce-cli containerd.io "
            "docker-buildx-plugin docker-compose-plugin") in rec


def test_install_debian_uses_debian_repo_for_debian(tmp_path):
    r, rec, tee = _dryrun("detect_os; install_docker_debian", tmp_path,
                          osr_kv=dict(ID="debian", VERSION_CODENAME="bookworm"))
    assert r.returncode == 0, r.stderr
    assert "https://download.docker.com/linux/debian/gpg" in rec
    assert "https://download.docker.com/linux/debian bookworm stable" in tee


def test_install_debian_derivative_prefers_ubuntu_codename(tmp_path):
    # Mint-like: VERSION_CODENAME is the derivative's, UBUNTU_CODENAME is the base
    r, rec, tee = _dryrun("detect_os; install_docker_debian", tmp_path,
                          osr_kv=dict(ID="linuxmint", VERSION_CODENAME="virginia", UBUNTU_CODENAME="jammy"))
    assert r.returncode == 0, r.stderr
    assert "https://download.docker.com/linux/ubuntu jammy stable" in tee
    assert "virginia" not in tee


def test_install_debian_missing_codename_returns_nonzero(tmp_path):
    r, rec, tee = _dryrun(
        "detect_os; if install_docker_debian; then echo RC=0; else echo RC=1; fi",
        tmp_path, osr_kv=dict(ID="ubuntu"))  # no VERSION_CODENAME/UBUNTU_CODENAME
    assert "RC=1" in r.stdout
    assert "codename" in (r.stdout + r.stderr).lower()


def test_install_rhel_uses_yum_without_dnf(tmp_path):
    body = ('command() { if [ "$1" = -v ] && [ "$2" = dnf ]; then return 1; fi; '
            'builtin command "$@"; }; install_docker_rhel')
    r, rec, _ = _dryrun(body, tmp_path, osr_kv=dict(ID="centos"))
    assert r.returncode == 0, r.stderr
    assert "yum -y install" in rec
    assert "download.docker.com/linux/centos/docker-ce.repo" in rec
    assert "docker-ce" in rec and "containerd.io" in rec


def test_install_rhel_prefers_dnf_when_present(tmp_path):
    body = ('command() { if [ "$1" = -v ] && [ "$2" = dnf ]; then return 0; fi; '
            'builtin command "$@"; }; install_docker_rhel')
    r, rec, _ = _dryrun(body, tmp_path, osr_kv=dict(ID="rocky"))
    assert r.returncode == 0, r.stderr
    assert "dnf -y install" in rec
    assert "docker-ce" in rec


def test_install_fedora_uses_fedora_repo(tmp_path):
    r, rec, _ = _dryrun("install_docker_fedora", tmp_path, osr_kv=dict(ID="fedora"))
    assert r.returncode == 0, r.stderr
    assert "dnf -y install dnf-plugins-core" in rec
    assert "download.docker.com/linux/fedora/docker-ce.repo" in rec
    assert "dnf -y install docker-ce" in rec


def test_install_arch_packages(tmp_path):
    r, rec, _ = _dryrun("install_docker_arch", tmp_path, osr_kv=dict(ID="arch"))
    assert r.returncode == 0, r.stderr
    assert "pacman -Syu --noconfirm --needed docker docker-compose docker-buildx" in rec


def test_install_suse_packages(tmp_path):
    r, rec, _ = _dryrun("install_docker_suse", tmp_path, osr_kv=dict(ID="sles"))
    assert r.returncode == 0, r.stderr
    assert "zypper --non-interactive install docker docker-compose" in rec


def test_install_convenience_downloads_and_runs(tmp_path):
    r, rec, _ = _dryrun("install_docker_convenience", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "CURL: -fsSL https://get.docker.com" in rec
    assert "AS_ROOT: sh " in rec


# --------------------------------------------------------------------------- #
# ensure_docker failure paths + daemon start + sudo fallback
# --------------------------------------------------------------------------- #
def test_ensure_docker_dies_when_compose_missing(tmp_path):
    binp = _fake_docker_bin(tmp_path, compose_ok=False)
    snippet = f'source "{SETUP}"; DOCKER=(docker); ensure_docker'
    r = _bash(snippet, PATH=f"{binp}:" + os.environ["PATH"])
    assert r.returncode != 0
    assert "docker compose" in (r.stdout + r.stderr).lower()


def test_ensure_docker_dies_when_daemon_unreachable(tmp_path):
    binp = _fake_docker_bin(tmp_path, info_ok=False)  # info fails, compose ok
    snippet = (
        f'source "{SETUP}"; DOCKER=(docker); '
        'try_root() { return 1; }; sudo() { return 1; }; '   # no real systemctl/sudo
        'ensure_docker'
    )
    r = _bash(snippet, PATH=f"{binp}:" + os.environ["PATH"])
    assert r.returncode != 0
    assert "not reachable" in (r.stdout + r.stderr).lower()


def test_ensure_docker_falls_back_to_sudo_docker(tmp_path):
    # plain docker can't reach the daemon, but `sudo docker` can — the fresh-install
    # (not-yet-in-docker-group) path. ensure_docker should rewrite DOCKER=(sudo docker).
    binp = _fake_docker_bin(tmp_path, info_ok=False, compose_ok=False)
    snippet = (
        f'source "{SETUP}"; DOCKER=(docker); '
        # the test process is root in CI; simulate a non-root user so the
        # not-in-docker-group sudo fallback branch is reachable
        'id() { if [ "$1" = -u ]; then echo 1000; else command id "$@"; fi; }; '
        'try_root() { return 1; }; '
        'sudo() { if [ "$1" = docker ]; then return 0; fi; return 0; }; '
        'ensure_docker; echo "FINAL_DOCKER=${DOCKER[*]}"'
    )
    r = _bash(snippet, PATH=f"{binp}:" + os.environ["PATH"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert "FINAL_DOCKER=sudo docker" in r.stdout
    assert "sudo docker" in (r.stdout + r.stderr)  # the advisory warn fired


def test_start_docker_daemon_attempts_systemd(tmp_path):
    binp = _fake_docker_bin(tmp_path, info_ok=False)
    rec = tmp_path / "tr.log"
    snippet = (
        f'source "{SETUP}"; DOCKER=(docker); REC="{rec}"; '
        'command() { if [ "$1" = -v ] && [ "$2" = systemctl ]; then return 0; fi; builtin command "$@"; }; '
        'try_root() { echo "TRYROOT: $*" >> "$REC"; return 0; }; '
        'start_docker_daemon'
    )
    r = _bash(snippet, PATH=f"{binp}:" + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert "systemctl enable --now docker" in rec.read_text()


# --------------------------------------------------------------------------- #
# pure / branching helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,expected", [
    ("https://h.io", "admin@h.io"),
    ("http://host:8000/x/y", "admin@host"),
    ("https://sub.dom.io/path", "admin@sub.dom.io"),
    ("", "admin@localhost"),
])
def test_default_email_from_url(url, expected):
    r = _bash(f'source "{SETUP}"; default_email_from_url "{url}"')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == expected


def test_derive_public_url_from_control_plane(tmp_path):
    envf = tmp_path / "e.env"
    envf.write_text('INFERIA_CONTROL_PLANE_EXTERNAL_URL="https://cp.host/api"\n')
    r = _bash(f'source "{SETUP}"; ENV_FILE="{envf}"; _derive_public_url')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "https://cp.host"


def test_http_code_retries_on_connection_failure(tmp_path):
    cnt = tmp_path / "n"
    snippet = (
        f'source "{SETUP}"; CNT="{cnt}"; echo 0 > "$CNT"; '
        'curl() { n=$(cat "$CNT"); n=$((n+1)); echo "$n" > "$CNT"; '
        'if [ "$n" -lt 3 ]; then printf ""; return 1; else printf "200"; fi; }; '
        'http_code "http://x"'
    )
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "200"


def test_check_route_accepts_and_rejects(tmp_path):
    snippet = (
        f'source "{SETUP}"; base="http://x"; failed=0; '
        'http_code() { echo "$STUBCODE"; }; '
        'STUBCODE=200 _check_route "/" "^200$"; '
        'STUBCODE=401 _check_route "/inf" "^(200|401)$"; '
        'STUBCODE=500 _check_route "/api" "^200$"; '
        'echo "FAILED=$failed"'
    )
    r = _bash(snippet)
    assert r.returncode == 0, r.stderr
    assert "FAILED=1" in r.stdout   # only the 500 route flips the flag


def test_generated_env_is_chmod_600(tmp_path):
    envf = tmp_path / "gen.env"
    r = _run(["--no-up", "--yes", "--public-url", "https://h.io",
              "--superadmin-email", "admin@h.io", "--superadmin-password", "password123",
              "--env-file", str(envf)], tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert stat.S_IMODE(envf.stat().st_mode) == 0o600


def test_unknown_arg_fails(tmp_path):
    r = _run(["--bogus-flag"], tmp_path)
    assert r.returncode != 0
    assert "unknown argument" in (r.stdout + r.stderr).lower()


def test_no_install_docker_flag_parses():
    r = _bash(f'source "{SETUP}"; parse_args --no-install-docker; echo "NID=$NO_INSTALL_DOCKER"')
    assert r.returncode == 0, r.stderr
    assert "NID=1" in r.stdout
