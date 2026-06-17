"""Helpers for forwarding inference to an InferiaLLM worker's :8080 proxy.

A worker-hosted deployment (ollama/vllm running on an EC2 worker) is reached
at its ``endpoint`` (the node's advertise_url, e.g. ``http://ec2-…:8080``).
That proxy:
  * authenticates ``/v1/*`` with ``Authorization: Bearer <pool inference_token>``
    (provisioned into the worker at bootstrap), and
  * routes to the correct model container via the ``X-Inferia-Deployment-Id``
    header (inferia-worker ``inference.PathResolver``).

When an ``envoy_url`` is configured, worker-hosted deploys are routed through
the front Envoy proxy instead of directly to the worker.  The Envoy uses the
``X-Inferia-Route-Cluster`` header to decide which upstream cluster to forward
to (see docs/specs/2026-06-17-envoy-xds-proxy.md).

These helpers compute the bearer + extra headers + upstream model so the
completion handler treats worker-hosted and external-provider deploys
correctly. They are pure (no I/O) for straightforward unit testing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .providers import is_external_engine

__all__ = ["provider_auth", "upstream_model", "envoy_route_headers"]

DEPLOYMENT_ID_HEADER = "X-Inferia-Deployment-Id"
ROUTE_CLUSTER_HEADER = "X-Inferia-Route-Cluster"


def _credentials(deployment: Dict[str, Any]) -> Dict[str, Any]:
    return (
        deployment.get("credentials_json")
        or deployment.get("configuration")
        or {}
    )


def provider_auth(
    deployment: Dict[str, Any], engine: str, internal_key: str,
) -> Tuple[str, Dict[str, str]]:
    """Return (provider_key, extra_headers) for the upstream request.

    Resolution:
      * If the resolved context carries a pool ``inference_token`` the deploy
        is worker-hosted: the token IS the bearer, and we add
        ``X-Inferia-Deployment-Id`` so the worker's :8080 proxy routes to the
        right model container. The token is authoritative — a stray config
        api_key must not be used to auth to our own worker proxy.
      * Else an external provider's own api_key/key/token (from
        credentials_json/configuration) is used.
      * Else, for a non-external engine with no key, fall back to the internal
        key (legacy behaviour).
    """
    inference_token = deployment.get("inference_token")
    if inference_token:
        return str(inference_token), {
            DEPLOYMENT_ID_HEADER: str(deployment.get("id") or ""),
        }

    creds = _credentials(deployment)
    provider_key = str(
        creds.get("api_key") or creds.get("key") or creds.get("token") or ""
    )
    if not provider_key and not is_external_engine(engine):
        provider_key = internal_key
    return provider_key, {}


def upstream_model(deployment: Dict[str, Any]) -> Optional[str]:
    """The model id to send upstream.

    For ollama the worker forwards verbatim, so this must be the real model
    tag (e.g. ``gemma3:4b``), NOT the human display name. Order:
    inference_model → configuration.model / model_id → model_name.
    """
    creds = _credentials(deployment)
    return (
        deployment.get("inference_model")
        or creds.get("model")
        or creds.get("model_id")
        or deployment.get("model_name")
    )


def envoy_route_headers(
    deployment: Dict[str, Any],
    envoy_url: str | None,
) -> Tuple[Optional[str], Dict[str, str]]:
    """Compute (envoy_url_or_None, extra_headers) for Envoy-based routing.

    When ``envoy_url`` is set AND the deployment is worker-hosted (carries
    an ``inference_token``), the caller should replace the worker's direct
    ``advertise_url`` with the returned Envoy URL and add the returned
    headers (which include the ``X-Inferia-Route-Cluster`` header).

    The cluster name is derived from the deployment's ``pool_id``:
    ``grp-<pool_id>``.  Nodes in the same pool share this cluster and are
    load-balanced together by the front Envoy.  If no ``pool_id`` is
    available the cluster defaults to ``inferia-workers`` (a flat cluster
    containing all healthy workers).

    When the deployment is NOT worker-hosted or ``envoy_url`` is not set,
    returns (None, {}) — no routing change needed.
    """
    inference_token = deployment.get("inference_token")
    if not inference_token or not envoy_url:
        return None, {}

    pool_id = deployment.get("pool_id")
    if pool_id:
        cluster = f"grp-{pool_id.replace('-', '')}"
    else:
        cluster = "inferia-workers"

    return envoy_url, {ROUTE_CLUSTER_HEADER: cluster}
