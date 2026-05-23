"""HTTP helpers for the Qwen3 smoke scripts.

This module tracks the *real* node-centric API surface as of the
2026-05-14 refactor that removed the `/v1/compute-pools` namespace.
The flow is now:

  1. `POST /auth/login` with {username, password} → {access_token}
  2. `POST /api/v1/nodes/add/worker` → {node_id, bootstrap_token,
     control_plane_url, inference_token, env_snippet, ...}
     The pool_id is embedded in the bootstrap_token JWT claim and is
     extracted by `add_worker_node` for the caller's convenience.
  3. `GET /api/v1/admin/workers/pool/{pool_id}` → list of WorkerView.
     Status field is `state`, not `status`.
  4. `POST /api/v1/deployment/deploy` proxies to the orchestration
     service's DeployModelRequest shape (model_name, model_version,
     replicas, gpu_per_replica, pool_id, engine, configuration).
  5. `DELETE /api/v1/nodes/{node_id}` → 204.

Chat completions are served by the inference data-plane on port
8001 (`/v1/chat/completions`). The smoke connects directly with the
admin access_token; we don't mint an org-scoped API key first.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import httpx


T = TypeVar("T")


class SmokeError(Exception):
    """Base class for all smoke errors."""


class APIError(SmokeError):
    def __init__(self, status: int, body: str, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class SmokeTimeoutError(SmokeError):
    pass


class EmptyResponseError(SmokeError):
    pass


class StreamTruncatedError(SmokeError):
    pass


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Parse a JWT payload without verifying — caller already trusts the issuer."""
    try:
        _, payload, _ = token.split(".", 2)
    except ValueError as e:
        raise SmokeError(f"malformed bootstrap_token JWT: {e}") from e
    padding = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(payload + padding)
    return json.loads(raw)


@dataclass
class WorkerNode:
    """Subset of the AddWorkerResponse the smoke actually uses."""

    node_id: str
    pool_id: str
    bootstrap_token: str
    control_plane_url: str
    inference_token: str
    env_snippet: str


@dataclass
class SmokeAPI:
    """Thin httpx wrapper used by the smoke scripts."""

    base_url: str
    inference_url: str | None = None
    timeout: float = 30.0
    _token: str | None = field(default=None, init=False)
    _client: httpx.Client | None = field(default=None, init=False)
    _inference_client: httpx.Client | None = field(default=None, init=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def _http_inference(self) -> httpx.Client:
        if self._inference_client is None:
            url = self.inference_url or self.base_url.replace(":8000", ":8001")
            self._inference_client = httpx.Client(base_url=url, timeout=self.timeout)
        return self._inference_client

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        kw.setdefault("headers", {}).update(self._auth_headers())
        resp = self._http().request(method, path, **kw)
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        return resp

    # ---- auth ----

    def login(self, username: str, password: str) -> None:
        """Login via gateway. `username` matches the LoginRequest model field."""
        resp = self._http().post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        self._token = resp.json()["access_token"]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._inference_client is not None:
            self._inference_client.close()
            self._inference_client = None

    # ---- node lifecycle ----

    def add_worker_node(
        self,
        *,
        node_name: str,
        advertise_url: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> WorkerNode:
        body: dict[str, Any] = {"node_name": node_name}
        if advertise_url:
            body["advertise_url"] = advertise_url
        if labels:
            body["labels"] = labels
        data = self._request("POST", "/api/v1/nodes/add/worker", json=body).json()
        claims = _decode_jwt_claims(data["bootstrap_token"])
        pool_id = claims.get("pool_id")
        if not pool_id:
            raise SmokeError("bootstrap_token JWT missing pool_id claim")
        return WorkerNode(
            node_id=data["node_id"],
            pool_id=pool_id,
            bootstrap_token=data["bootstrap_token"],
            control_plane_url=data["control_plane_url"],
            inference_token=data["inference_token"],
            env_snippet=data["env_snippet"],
        )

    def add_provider_node(
        self,
        *,
        provider: str,
        node_name: str | None = None,
        spec: dict[str, Any] | None = None,
        credential_name: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Provision a provider-backed node (AWS, GCP, Nosana, ...).

        Pool_id for the resulting node is read from the response. The
        underlying provisioning is asynchronous; poll worker state for
        readiness.
        """
        body: dict[str, Any] = {"spec": spec or {}, "labels": labels or {}}
        if node_name:
            body["node_name"] = node_name
        if credential_name:
            body["credential_name"] = credential_name
        return self._request(
            "POST", f"/api/v1/nodes/add/{provider}", json=body
        ).json()

    def list_workers(self, pool_id: str) -> list[dict[str, Any]]:
        return self._request(
            "GET", f"/api/v1/admin/workers/pool/{pool_id}"
        ).json()["workers"]

    def delete_node(self, node_id: str) -> None:
        """Idempotent: 404 treated as already gone."""
        try:
            self._request("DELETE", f"/api/v1/nodes/{node_id}")
        except APIError as e:
            if e.status != 404:
                raise

    # ---- deployments ----

    def deploy_model(
        self,
        *,
        pool_id: str,
        model_name: str,
        model_version: str,
        engine: str,
        replicas: int = 1,
        gpu_per_replica: int = 0,
        configuration: dict[str, Any] | None = None,
        workload_type: str = "inference",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "pool_id": pool_id,
            "model_name": model_name,
            "model_version": model_version,
            "engine": engine,
            "replicas": replicas,
            "gpu_per_replica": gpu_per_replica,
            "workload_type": workload_type,
        }
        if configuration is not None:
            body["configuration"] = configuration
        return self._request("POST", "/api/v1/deployment/deploy", json=body).json()

    def get_deployment_status(self, deployment_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/api/v1/deployment/status/{deployment_id}"
        ).json()

    def delete_deployment(self, deployment_id: str) -> None:
        # Orchestration requires terminate-before-delete. Try terminate first,
        # poll briefly for the state to settle, then delete. 404s are fine —
        # the deployment may already be gone if the test cleaned up early.
        try:
            self._request(
                "POST",
                "/api/v1/deployment/terminate",
                json={"deployment_id": deployment_id},
            )
        except APIError as e:
            if e.status not in (404, 400):
                raise
        for _ in range(20):
            try:
                s = self.get_deployment_status(deployment_id)
            except APIError as e:
                if e.status == 404:
                    return
                raise
            state = (s.get("state") or s.get("status") or "").upper()
            if state in {"TERMINATED", "STOPPED", "FAILED", "DELETED"}:
                break
            time.sleep(1.0)
        try:
            self._request("DELETE", f"/api/v1/deployment/delete/{deployment_id}")
        except APIError as e:
            if e.status != 404:
                raise

    # ---- chat (inference data-plane on :8001) ----

    def chat(
        self,
        model: str,
        prompt: str,
        *,
        stream: bool = False,
        timeout: float = 60.0,
    ) -> str:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        # Use sandbox mode so the gateway JWT is accepted as the api_key —
        # smoke runs as superadmin and has no DB-stored inference API key.
        sandbox_headers = {**self._auth_headers(), "x-sandbox": "true"}
        if not stream:
            resp = self._http_inference().post(
                "/v1/chat/completions",
                json=body,
                headers=sandbox_headers,
                timeout=timeout,
            )
            if resp.status_code >= 400:
                raise APIError(resp.status_code, resp.text)
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                raise EmptyResponseError("assistant content empty")
            return content

        out: list[str] = []
        saw_done = False
        with self._http_inference().stream(
            "POST",
            "/v1/chat/completions",
            json=body,
            headers=sandbox_headers,
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 400:
                raise APIError(resp.status_code, resp.read().decode())
            for line in resp.iter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:
                    continue
                delta = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                )
                if delta:
                    out.append(delta)
        if not saw_done:
            raise StreamTruncatedError("stream ended without [DONE]")
        full = "".join(out)
        if not full:
            raise EmptyResponseError("stream produced no content")
        return full


INSTANCE_HOURLY_USD = {
    "g4dn.xlarge": 0.526,
    "g5.xlarge": 1.006,
    "g6.xlarge": 0.805,
}


def wait_until(
    predicate: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = 2.0,
    tolerate_status: set[int] = frozenset({503, 504}),
) -> T:
    """Poll `predicate` until it returns truthy or `timeout` elapses.

    APIError with status in `tolerate_status` is swallowed (counts as not-yet).
    Any other APIError propagates. SmokeTimeoutError is raised on deadline.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            v = predicate()
        except APIError as e:
            if e.status not in tolerate_status:
                raise
            v = None
        if v:
            return v
        if time.monotonic() >= deadline:
            raise SmokeTimeoutError(f"timed out after {timeout}s")
        time.sleep(interval)


def cost_estimate(instance_type: str, hours: float) -> str:
    rate = INSTANCE_HOURLY_USD.get(instance_type, 0.0)
    total = rate * hours
    return f"{instance_type} × {hours:.2f}h ≈ ${total:.3f}"
