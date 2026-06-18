"""Resolve internal-service localhost targets from their port env vars.

The unified app co-locates several internal listeners on loopback:

  * orchestration REST  — ``HTTP_PORT``           (default 8080)
  * orchestration gRPC  — ``GRPC_PORT``           (default 50051)
  * DePIN sidecar (Node)— ``DEPIN_SIDECAR_PORT``  (default 3000)

In the default (bridge-network) container these ports are private, but when the
app is run on HOST networking they can collide with other services on the host.
Each is therefore overridable via its port env var. The in-process CLIENTS derive
their localhost target from the SAME var so that changing one variable moves both
the server bind and every caller — no need to also hand-edit the matching
``*_URL`` / ``*_ADDR``.

An explicit ``ORCHESTRATION_URL`` / ``ORCHESTRATION_GRPC_ADDR`` /
``NOSANA_SIDECAR_URL`` / ``AKASH_SIDECAR_URL`` still wins (split-mode / remote
deployments, where the peer is a different host) — these helpers only supply the
co-located DEFAULT.
"""
from __future__ import annotations

import os

# Canonical defaults — keep in sync with .env / .env.example.
DEFAULT_HTTP_PORT = "8080"
DEFAULT_GRPC_PORT = "50051"
DEFAULT_DEPIN_SIDECAR_PORT = "3000"


def orchestration_http_port() -> str:
    return os.getenv("HTTP_PORT", DEFAULT_HTTP_PORT)


def orchestration_grpc_port() -> str:
    return os.getenv("GRPC_PORT", DEFAULT_GRPC_PORT)


def depin_sidecar_port() -> str:
    return os.getenv("DEPIN_SIDECAR_PORT", DEFAULT_DEPIN_SIDECAR_PORT)


def orchestration_http_url() -> str:
    """Co-located base URL of the orchestration REST server.

    Explicit ``ORCHESTRATION_URL`` wins; else ``http://localhost:<HTTP_PORT>``.
    """
    return os.getenv("ORCHESTRATION_URL") or f"http://localhost:{orchestration_http_port()}"


def orchestration_grpc_addr(host: str = "127.0.0.1") -> str:
    """Co-located dial address of the orchestration gRPC server.

    Explicit ``ORCHESTRATION_GRPC_ADDR`` wins; else ``<host>:<GRPC_PORT>``.
    """
    return os.getenv("ORCHESTRATION_GRPC_ADDR") or f"{host}:{orchestration_grpc_port()}"


def depin_sidecar_url(suffix: str = "", *, env_var: str = "NOSANA_SIDECAR_URL") -> str:
    """Co-located URL of the DePIN sidecar, optionally with a path ``suffix``
    (e.g. ``/akash``, ``/nosana``).

    An explicit value of ``env_var`` wins (used verbatim, suffix NOT appended —
    the operator gave the full URL); else ``http://localhost:<DEPIN_SIDECAR_PORT><suffix>``.
    """
    explicit = os.getenv(env_var)
    if explicit:
        return explicit
    return f"http://localhost:{depin_sidecar_port()}{suffix}"
