"""Host-port preflight for setup.sh (plan.md WS8: "a port pre-flight check in
setup.sh (8080 clashed during our own test)").

Two layers:
1. ``inferia_setup`` helper: which host ports the compose stack publishes (from
   ``docker compose config``), which env var controls each, and whether each is
   free (real sockets, other containers, duplicates within the stack).
2. ``setup.sh port_preflight`` driven end-to-end against a fake ``docker`` on
   PATH, with a REAL listening socket occupying a port.

Unlike test_setup_sh.py this file is not in CI's ignore list.
"""
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"
_HELPER = REPO / "scripts" / "setup" / "inferia_setup.py"
sys.path.insert(0, str(_HELPER.parent))
import inferia_setup as mod  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures: what `docker compose config` prints for docker-compose.yml
# --------------------------------------------------------------------------- #
def _config(app_port=8000, orch=8080, envoy=10000):
    return {
        "name": "deploy",
        "services": {
            "app": {"ports": [
                {"mode": "ingress", "target": app_port, "published": str(app_port), "protocol": "tcp"},
                {"mode": "ingress", "host_ip": "127.0.0.1", "target": 8080,
                 "published": str(orch), "protocol": "tcp"},
            ]},
            "front-envoy": {"ports": [
                {"mode": "ingress", "target": 10000, "published": str(envoy), "protocol": "tcp"},
            ]},
            "postgres": {},
            "redis": {"ports": None},
        },
    }


RAW = {
    "name": "deploy",
    "services": {
        "app": {"ports": [
            "${APP_PORT:-8000}:${APP_PORT:-8000}",
            "127.0.0.1:${ORCHESTRATION_HOST_PORT:-8080}:8080",
        ]},
        "front-envoy": {"ports": ["${ENVOY_HOST_PORT:-10000}:10000"]},
        "postgres": {},
        "redis": {},
    },
}


@pytest.fixture
def listener():
    """A real TCP listener on 127.0.0.1:<ephemeral>, like another service holding the port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen()
    yield s.getsockname()[1]
    s.close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def test_bindings_from_compose_config_with_env_vars():
    b = mod.compose_port_bindings(_config(), RAW)
    assert [(x.service, x.host_ip, x.port, x.protocol, x.env_var) for x in b] == [
        ("app", "", 8000, "tcp", "APP_PORT"),
        ("app", "127.0.0.1", 8080, "tcp", "ORCHESTRATION_HOST_PORT"),
        ("front-envoy", "", 10000, "tcp", "ENVOY_HOST_PORT"),
    ]


def test_env_var_unknown_when_hardcoded_or_misaligned():
    raw = {"services": {"app": {"ports": ["8000:8000"]}, "front-envoy": {"ports": []}}}
    b = mod.compose_port_bindings(_config(), raw)
    assert all(x.env_var is None for x in b)


@pytest.mark.parametrize("entry, var", [
    ("${A:-1}:${A:-1}", "A"),
    ("127.0.0.1:${B}:80", "B"),
    ("${HOST_IP}:${C:-9}:80", "C"),        # host-ip var is not the port var
    ("[::1]:${D-5}:80/udp", "D"),
    ("$E:80", "E"),
    ("8000:80", None),
    ("80", None),                          # target only: ephemeral host port
    ({"published": "${F:-1}", "target": 80}, "F"),
    ({"published": 1, "target": 80}, None),
])
def test_env_var_extraction(entry, var):
    assert mod._published_env_var(entry) == var


def test_published_ranges_and_ephemeral_are_handled():
    cfg = {"services": {"s": {"ports": [
        {"target": 80, "published": "9000-9002", "protocol": "tcp"},
        {"target": 81, "protocol": "udp"},  # no published → ephemeral, nothing to check
    ]}}}
    b = mod.compose_port_bindings(cfg, {"services": {}})
    assert [x.port for x in b] == [9000, 9001, 9002]


def test_docker_ps_ports_parsing():
    text = (
        "deploy\tinferia-app\t0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp, 127.0.0.1:8080->8080/tcp\n"
        "\tsome-other\t0.0.0.0:9000-9001->9000-9001/tcp, 6379/tcp\n"
        "proj2\tudp-thing\t127.0.0.1:5353->53/udp\n"
        "\n"
    )
    pubs = mod.parse_docker_ps(text)
    assert ("deploy", "inferia-app", "0.0.0.0", 8000, "tcp") in pubs
    assert ("deploy", "inferia-app", "::", 8000, "tcp") in pubs
    assert ("", "some-other", "0.0.0.0", 9001, "tcp") in pubs
    assert ("proj2", "udp-thing", "127.0.0.1", 5353, "udp") in pubs
    assert not any(p[3] == 6379 for p in pubs)  # unpublished


# --------------------------------------------------------------------------- #
# probing real sockets
# --------------------------------------------------------------------------- #
def test_port_is_free_detects_real_listener(listener):
    assert mod.port_is_free("127.0.0.1", listener, "tcp") is False
    # all-interfaces publish also collides with a loopback-only listener (Linux)
    assert mod.port_is_free("", listener, "tcp") is False


def test_port_is_free_true_for_unused_port():
    assert mod.port_is_free("127.0.0.1", _free_port(), "tcp") is True
    assert mod.port_is_free("", _free_port(), "tcp") is True


def test_port_is_free_udp(listener):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    try:
        assert mod.port_is_free("127.0.0.1", s.getsockname()[1], "udp") is False
    finally:
        s.close()


def test_port_is_free_privileged_port_is_not_reported_in_use():
    # Non-root can't bind <1024 (EACCES); docker can, so that is not a conflict.
    if os.geteuid() == 0:
        pytest.skip("root can bind privileged ports")
    assert mod.port_is_free("127.0.0.1", 1, "tcp") is True


def test_port_is_free_unavailable_address_raises():
    with pytest.raises(mod.PortProbeError, match="not available"):
        mod.port_is_free("203.0.113.7", _free_port(), "tcp")  # TEST-NET-3, not local


# --------------------------------------------------------------------------- #
# conflict detection
# --------------------------------------------------------------------------- #
def test_check_ports_clean_when_all_free():
    b = mod.compose_port_bindings(_config(), RAW)
    assert mod.check_ports(b, is_free=lambda *a: True) == []


def test_check_ports_flags_process_and_names_env_var():
    b = mod.compose_port_bindings(_config(), RAW)
    conflicts = mod.check_ports(b, is_free=lambda ip, port, proto: port != 8080)
    (c,) = conflicts
    assert c.binding.port == 8080 and c.binding.env_var == "ORCHESTRATION_HOST_PORT"
    assert c.holder is None  # an ordinary process, not a container


def test_check_ports_names_other_container_even_if_socket_probe_passes():
    # userland-proxy=false: docker publishes via iptables, no host socket.
    b = mod.compose_port_bindings(_config(), RAW)
    others = [("other", "grafana", "0.0.0.0", 10000, "tcp")]
    (c,) = mod.check_ports(b, published=others, project="deploy", is_free=lambda *a: True)
    assert c.binding.port == 10000 and c.holder == "container grafana"


def test_check_ports_ignores_ports_held_by_this_project():
    # Re-running setup.sh while the stack is up must not fail on its own ports.
    b = mod.compose_port_bindings(_config(), RAW)
    own = [("deploy", "inferia-app", "0.0.0.0", 8000, "tcp"),
           ("deploy", "inferia-app", "127.0.0.1", 8080, "tcp"),
           ("deploy", "inferia-envoy", "0.0.0.0", 10000, "tcp")]
    assert mod.check_ports(b, published=own, project="deploy", is_free=lambda *a: False) == []


def test_check_ports_other_container_on_different_ip_is_no_conflict():
    b = mod.compose_port_bindings(_config(), RAW)  # 8080 is published on 127.0.0.1 only
    others = [("x", "c", "192.168.1.5", 8080, "tcp"), ("x", "c", "127.0.0.1", 8080, "udp")]
    assert mod.check_ports(b, published=others, project="deploy", is_free=lambda *a: True) == []


def test_check_ports_flags_duplicates_within_stack():
    b = mod.compose_port_bindings(_config(app_port=8080, orch=8080), RAW)
    conflicts = mod.check_ports(b, is_free=lambda *a: True)
    assert len(conflicts) == 1
    assert "twice" in conflicts[0].reason


def test_suggest_free_port_skips_taken_and_stack_ports():
    taken = {8081, 8082}
    got = mod.suggest_free_port(8080, "127.0.0.1", "tcp",
                                is_free=lambda ip, p, proto: p not in taken, reserved={8083})
    assert got == 8084
    assert mod.suggest_free_port(65535, "", "tcp", is_free=lambda *a: False) is None


def test_format_message_is_actionable():
    b = mod.compose_port_bindings(_config(), RAW)
    conflicts = mod.check_ports(b, is_free=lambda ip, port, proto: port not in (8000, 8080, 10000))
    text = mod.format_conflicts(conflicts, env_file="/x/.env", is_free=lambda *a: True)
    assert "Host port 8000/tcp" in text and "APP_PORT" in text
    assert "ORCHESTRATION_HOST_PORT" in text and "--orchestration-url" in text
    assert "ENVOY_HOST_PORT" in text
    assert "/x/.env" in text
    assert "e.g. ORCHESTRATION_HOST_PORT=8081" in text


def test_format_message_without_env_var_points_at_compose_file():
    b = mod.compose_port_bindings(_config(), {"services": {}})
    conflicts = mod.check_ports(b, is_free=lambda ip, port, proto: port != 10000)
    text = mod.format_conflicts(conflicts, env_file="/x/.env", is_free=lambda *a: True)
    assert "docker-compose.yml" in text and "front-envoy" in text


# --------------------------------------------------------------------------- #
# CLI: inferia_setup.py check-ports
# --------------------------------------------------------------------------- #
def _cli(tmp_path, cfg, raw=RAW, ps="", extra=()):
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    (tmp_path / "r.json").write_text(json.dumps(raw))
    (tmp_path / "ps.txt").write_text(ps)
    return subprocess.run(
        [sys.executable, str(_HELPER), "check-ports", "--config", str(tmp_path / "c.json"),
         "--raw", str(tmp_path / "r.json"), "--docker-ps", str(tmp_path / "ps.txt"),
         "--env-file", "/x/.env", *extra],
        capture_output=True, text=True,
    )


def test_cli_ok(tmp_path):
    p = [_free_port() for _ in range(3)]
    r = _cli(tmp_path, _config(*p))
    assert r.returncode == 0, r.stderr
    assert str(p[0]) in r.stdout


def test_cli_conflict_exit_code_and_message(tmp_path, listener):
    r = _cli(tmp_path, _config(_free_port(), listener, _free_port()))
    assert r.returncode == 3
    assert f"Host port {listener}/tcp" in r.stderr and "ORCHESTRATION_HOST_PORT" in r.stderr


def test_cli_bad_json_is_exit_2(tmp_path):
    (tmp_path / "c.json").write_text("not json")
    (tmp_path / "r.json").write_text("{}")
    r = subprocess.run([sys.executable, str(_HELPER), "check-ports", "--config",
                        str(tmp_path / "c.json"), "--raw", str(tmp_path / "r.json")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "compose config" in r.stderr


def test_cli_unavailable_host_ip_is_reported(tmp_path):
    cfg = {"name": "deploy", "services": {"app": {"ports": [
        {"target": 80, "published": str(_free_port()), "host_ip": "203.0.113.7", "protocol": "tcp"}]}}}
    r = _cli(tmp_path, cfg, raw={"services": {}})
    assert r.returncode == 3 and "not available" in r.stderr


# --------------------------------------------------------------------------- #
# setup.sh port_preflight, end-to-end with a fake docker on PATH
# --------------------------------------------------------------------------- #
def _fake_docker(tmp_path, cfg, raw=RAW, ps="", config_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    (tmp_path / "r.json").write_text(json.dumps(raw))
    (tmp_path / "ps.txt").write_text(ps)
    log = tmp_path / "docker.log"
    script = f"""#!/usr/bin/env bash
echo "$*" >> "{log}"
case "$*" in
  *"config --no-interpolate --format json"*) cat "{tmp_path / 'r.json'}" ;;
  *"config --format json"*) [ {config_rc} -eq 0 ] || {{ echo "boom: bad compose" >&2; exit {config_rc}; }}; cat "{tmp_path / 'c.json'}" ;;
  "ps "*) cat "{tmp_path / 'ps.txt'}" ;;
  *) echo "unexpected: $*" >&2; exit 99 ;;
esac
"""
    d = bindir / "docker"
    d.write_text(script)
    d.chmod(d.stat().st_mode | stat.S_IEXEC)
    return bindir, log


def _preflight(tmp_path, bindir):
    envf = tmp_path / "x.env"
    envf.write_text("APP_PORT=1\n")
    snippet = (f'source "{SETUP}"; ENV_FILE="{envf}"; PYTHON="{sys.executable}"; '
               f'DOCKER=(docker); port_preflight')
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "NO_COLOR": "1"}
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, env=env)


def test_setup_preflight_passes_when_ports_free(tmp_path):
    bindir, log = _fake_docker(tmp_path, _config(_free_port(), _free_port(), _free_port()))
    r = _preflight(tmp_path, bindir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Host ports free" in r.stdout
    calls = log.read_text()
    assert "--env-file" in calls  # resolved with the same env file compose up uses


def test_setup_preflight_fails_on_real_listener(tmp_path, listener):
    bindir, _ = _fake_docker(tmp_path, _config(_free_port(), listener, _free_port()))
    r = _preflight(tmp_path, bindir)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert f"Host port {listener}/tcp" in out
    assert "ORCHESTRATION_HOST_PORT" in out


def test_setup_preflight_reports_compose_config_failure(tmp_path):
    bindir, _ = _fake_docker(tmp_path, _config(), config_rc=1)
    r = _preflight(tmp_path, bindir)
    assert r.returncode != 0
    assert "boom: bad compose" in r.stdout + r.stderr


def test_setup_runs_preflight_before_compose_up():
    # main(): port_preflight must sit between preflight and compose_up.
    text = SETUP.read_text()
    main = text[text.index("main() {"):]
    assert main.index("port_preflight") < main.index("compose_up")
    assert main.index("  preflight\n") < main.index("port_preflight")


def test_skip_flag_documented_and_parsed(tmp_path):
    r = subprocess.run(["bash", str(SETUP), "--help"], capture_output=True, text=True)
    assert "--skip-port-check" in r.stdout
    snippet = f'source "{SETUP}"; parse_args --skip-port-check; echo "skip=$SKIP_PORT_CHECK"'
    r = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    assert "skip=1" in r.stdout


def test_compose_host_ports_are_configurable_with_unchanged_defaults():
    text = (REPO / "docker-compose.yml").read_text()
    assert '"127.0.0.1:${ORCHESTRATION_HOST_PORT:-8080}:8080"' in text
    assert '"${ENVOY_HOST_PORT:-10000}:10000"' in text
    assert '"${APP_PORT:-8000}:${APP_PORT:-8000}"' in text


def test_force_regeneration_keeps_host_port_overrides():
    fresh = {"ORCHESTRATION_HOST_PORT": "8080", "ENVOY_HOST_PORT": "10000", "APP_PORT": "8000"}
    existing = {"ORCHESTRATION_HOST_PORT": "18080", "ENVOY_HOST_PORT": "", "APP_PORT": "9000"}
    merged = mod.merge_preserve(existing, fresh)
    assert merged["ORCHESTRATION_HOST_PORT"] == "18080"
    assert merged["ENVOY_HOST_PORT"] == "10000"  # blank existing -> default
    assert merged["APP_PORT"] == "8000"  # APP_PORT follows --app-port, as before


# --------------------------------------------------------------------------- #
# in-process CLI + error branches (subprocess runs above don't attach coverage)
# --------------------------------------------------------------------------- #
def _write_inputs(tmp_path, cfg, raw=RAW, ps=""):
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    (tmp_path / "r.json").write_text(json.dumps(raw))
    (tmp_path / "ps.txt").write_text(ps)
    return ["check-ports", "--config", str(tmp_path / "c.json"), "--raw", str(tmp_path / "r.json"),
            "--docker-ps", str(tmp_path / "ps.txt"), "--env-file", "/x/.env"]


def test_main_check_ports_ok_and_conflict(tmp_path, listener, capsys):
    assert mod.main(_write_inputs(tmp_path, _config(_free_port(), _free_port(), _free_port()))) == 0
    assert mod.main(_write_inputs(tmp_path, _config(_free_port(), listener, _free_port()))) == 3
    assert "ORCHESTRATION_HOST_PORT" in capsys.readouterr().err


def test_main_check_ports_bad_json(tmp_path, capsys):
    args = _write_inputs(tmp_path, {})
    (tmp_path / "c.json").write_text("{")
    assert mod.main(args) == 2
    assert "compose config" in capsys.readouterr().err


def test_main_check_ports_names_container_from_docker_ps(tmp_path, capsys):
    p = _free_port()
    ps = f"other\tgrafana\t0.0.0.0:{p}->3000/tcp\nbroken line without tabs\n"
    assert mod.main(_write_inputs(tmp_path, _config(_free_port(), _free_port(), p), ps=ps)) == 3
    assert "container grafana" in capsys.readouterr().err


def test_probe_permission_denied_is_not_a_conflict(monkeypatch):
    import errno

    class S:
        def __init__(self, *a): pass
        def setsockopt(self, *a): pass
        def bind(self, addr): raise OSError(errno.EACCES, "denied")
        def close(self): pass

    monkeypatch.setattr(socket, "socket", S)
    assert mod.port_is_free("127.0.0.1", 80, "tcp") is True


def test_probe_unexpected_error_is_a_probe_error(monkeypatch):
    import errno

    class S:
        def __init__(self, *a): pass
        def setsockopt(self, *a): pass
        def bind(self, addr): raise OSError(errno.EINVAL, "weird")
        def close(self): pass

    monkeypatch.setattr(socket, "socket", S)
    with pytest.raises(mod.PortProbeError, match="cannot check"):
        mod.port_is_free("127.0.0.1", 80, "tcp")


def test_probe_skips_unsupported_family(monkeypatch):
    real = socket.socket
    port = _free_port()

    def fake(family, kind):
        if family == socket.AF_INET6:
            raise OSError("no ipv6")
        return real(family, kind)

    monkeypatch.setattr(socket, "socket", fake)
    assert mod.port_is_free("", port, "tcp") is True


def test_ipv6_host_ip_is_probed_on_ipv6():
    try:
        assert mod.port_is_free("::1", _free_port(), "tcp") is True
    except mod.PortProbeError:
        pytest.skip("no IPv6 loopback")


def test_suggest_gives_up_on_probe_error():
    def boom(*a):
        raise mod.PortProbeError("x")

    assert mod.suggest_free_port(8080, "1.2.3.4", "tcp", is_free=boom) is None


def test_airgap_check_follows_orchestration_host_port(tmp_path):
    # scripts/airgap_check.sh calls orchestration from the host; it must use the
    # published host port, not a hard-coded 8080.
    script = (REPO / "scripts" / "airgap_check.sh").read_text()
    assert "localhost:8080" not in script
    body = script[script.index("need_key() {"):]
    body = body[: body.index("\n}\n") + 3]
    prelude = 'set -euo pipefail\nfail() { echo "FAIL $1"; exit 1; }\n'  # the script's own options
    cases = (
        ('INTERNAL_API_KEY="k"\nORCHESTRATION_HOST_PORT="18080"\n', "18080"),
        ('INTERNAL_API_KEY="k"\n', "8080"),  # an .env from before the variable existed
        ("", "FAIL INTERNAL_API_KEY not found in .env"),  # the friendly error is reachable
    )
    for env_text, want in cases:
        (tmp_path / ".env").write_text(env_text)
        r = subprocess.run(["bash", "-c", f'{prelude}{body}need_key; echo "$ORCH_PORT"'],
                           capture_output=True, text=True, cwd=tmp_path)
        assert r.stdout.strip() == want, r.stdout + r.stderr
