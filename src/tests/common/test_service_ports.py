"""Tests for common.service_ports — internal-port → localhost-target derivation.

These resolve the co-located default for each internal service from its port env
var (HTTP_PORT / GRPC_PORT / DEPIN_SIDECAR_PORT), so a single var remaps both the
server bind and its in-process callers when running on host networking. An
explicit *_URL / *_ADDR always wins (split / remote deployments).
"""
import pytest

from common import service_ports as sp

# Every env var these helpers read — cleared before each case so the test sees
# the documented defaults regardless of the ambient .env.
_VARS = (
    "HTTP_PORT", "GRPC_PORT", "DEPIN_SIDECAR_PORT",
    "ORCHESTRATION_URL", "ORCHESTRATION_GRPC_ADDR",
    "NOSANA_SIDECAR_URL", "AKASH_SIDECAR_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


# ---- orchestration REST ----------------------------------------------------

def test_http_url_default():
    assert sp.orchestration_http_url() == "http://localhost:8080"


def test_http_url_follows_http_port(monkeypatch):
    monkeypatch.setenv("HTTP_PORT", "18080")
    assert sp.orchestration_http_url() == "http://localhost:18080"


def test_http_url_explicit_wins(monkeypatch):
    monkeypatch.setenv("HTTP_PORT", "18080")
    monkeypatch.setenv("ORCHESTRATION_URL", "http://orch.internal:9999")
    assert sp.orchestration_http_url() == "http://orch.internal:9999"


# ---- orchestration gRPC ----------------------------------------------------

def test_grpc_addr_default_host():
    assert sp.orchestration_grpc_addr() == "127.0.0.1:50051"


def test_grpc_addr_custom_host():
    assert sp.orchestration_grpc_addr("localhost") == "localhost:50051"


def test_grpc_addr_follows_grpc_port(monkeypatch):
    monkeypatch.setenv("GRPC_PORT", "55051")
    assert sp.orchestration_grpc_addr() == "127.0.0.1:55051"
    assert sp.orchestration_grpc_addr("localhost") == "localhost:55051"


def test_grpc_addr_explicit_wins(monkeypatch):
    monkeypatch.setenv("GRPC_PORT", "55051")
    monkeypatch.setenv("ORCHESTRATION_GRPC_ADDR", "orch:6000")
    assert sp.orchestration_grpc_addr() == "orch:6000"
    # host arg is ignored when explicit addr is set
    assert sp.orchestration_grpc_addr("localhost") == "orch:6000"


# ---- DePIN sidecar ---------------------------------------------------------

def test_sidecar_url_default_bare():
    assert sp.depin_sidecar_url() == "http://localhost:3000"


@pytest.mark.parametrize("suffix", ["", "/nosana", "/akash"])
def test_sidecar_url_suffix_and_port(monkeypatch, suffix):
    monkeypatch.setenv("DEPIN_SIDECAR_PORT", "3050")
    assert sp.depin_sidecar_url(suffix) == f"http://localhost:3050{suffix}"


def test_sidecar_url_explicit_nosana_wins_verbatim(monkeypatch):
    """Explicit NOSANA_SIDECAR_URL is used as-is — the suffix is NOT appended
    (operator supplied the full URL)."""
    monkeypatch.setenv("DEPIN_SIDECAR_PORT", "3050")
    monkeypatch.setenv("NOSANA_SIDECAR_URL", "http://sidecar.internal:7000")
    assert sp.depin_sidecar_url("/nosana", env_var="NOSANA_SIDECAR_URL") == "http://sidecar.internal:7000"


def test_sidecar_url_akash_uses_its_own_env_var(monkeypatch):
    """The akash consumer keys off AKASH_SIDECAR_URL, independent of NOSANA's."""
    monkeypatch.setenv("AKASH_SIDECAR_URL", "http://akash-sidecar:8001/akash")
    assert sp.depin_sidecar_url("/akash", env_var="AKASH_SIDECAR_URL") == "http://akash-sidecar:8001/akash"
    # NOSANA env must NOT bleed into the akash lookup
    monkeypatch.setenv("NOSANA_SIDECAR_URL", "http://nosana:7000")
    assert sp.depin_sidecar_url("/akash", env_var="AKASH_SIDECAR_URL") == "http://akash-sidecar:8001/akash"


def test_port_helpers_track_env(monkeypatch):
    assert (sp.orchestration_http_port(), sp.orchestration_grpc_port(),
            sp.depin_sidecar_port()) == ("8080", "50051", "3000")
    monkeypatch.setenv("HTTP_PORT", "1")
    monkeypatch.setenv("GRPC_PORT", "2")
    monkeypatch.setenv("DEPIN_SIDECAR_PORT", "3")
    assert (sp.orchestration_http_port(), sp.orchestration_grpc_port(),
            sp.depin_sidecar_port()) == ("1", "2", "3")
