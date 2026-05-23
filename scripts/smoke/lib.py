"""HTTP helpers for the Qwen3 smoke scripts.

Public surface mirrors the spec §5.2. Pure Python; tests use respx mocks.
"""
from __future__ import annotations

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


@dataclass
class SmokeAPI:
    """Thin httpx wrapper used by the smoke scripts."""

    base_url: str
    timeout: float = 30.0
    _token: str | None = field(default=None, init=False)
    _client: httpx.Client | None = field(default=None, init=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        kw.setdefault("headers", {}).update(self._auth_headers())
        resp = self._http().request(method, path, **kw)
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        return resp

    # ---- auth ----

    def login(self, email: str, password: str) -> None:
        resp = self._http().post("/v1/auth/login", json={"email": email, "password": password})
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        self._token = resp.json()["access_token"]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- pool ----

    def create_pool(
        self,
        *,
        provider: str,
        name: str,
        instance_type: str | None = None,
        region: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {"provider": provider, "name": name}
        if instance_type:
            body["instance_type"] = instance_type
        if region:
            body["region"] = region
        if metadata:
            body["metadata"] = metadata
        return self._request("POST", "/v1/compute-pools", json=body).json()["id"]

    def destroy_pool(self, pool_id: str) -> None:
        """Idempotent: 404 is treated as already destroyed."""
        try:
            self._request("POST", f"/v1/compute-pools/{pool_id}:destroy")
        except APIError as e:
            if e.status != 404:
                raise

    # ---- workers ----

    def mint_bootstrap_token(self, pool_id: str, ttl_hours: int) -> dict[str, Any]:
        if not (1 <= ttl_hours <= 24):
            raise ValueError(f"ttl_hours must be 1..24, got {ttl_hours}")
        return self._request(
            "POST",
            "/v1/admin/workers/mint",
            json={"pool_id": pool_id, "ttl_hours": ttl_hours},
        ).json()

    def list_workers(self, pool_id: str) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/admin/workers", params={"pool": pool_id}).json()["workers"]

    # ---- deployments ----

    def create_deployment(
        self,
        *,
        pool_id: str,
        recipe: str,
        model_uri: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "pool_id": pool_id,
            "recipe": recipe,
            "model_uri": model_uri,
            "name": name,
        }
        if config:
            body["config"] = config
        return self._request("POST", "/v1/deployments", json=body).json()["deployment_id"]

    def delete_deployment(self, deployment_id: str) -> None:
        try:
            self._request("DELETE", f"/v1/deployments/{deployment_id}")
        except APIError as e:
            if e.status != 404:
                raise

    def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/deployments/{deployment_id}").json()

    # ---- chat ----

    def chat(
        self,
        deployment_id: str,
        prompt: str,
        *,
        stream: bool = False,
        timeout: float = 60.0,
    ) -> str:
        body = {
            "deployment_id": deployment_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if not stream:
            resp = self._http().post(
                "/v1/inference/chat/completions",
                json=body,
                headers=self._auth_headers(),
                timeout=timeout,
            )
            if resp.status_code >= 400:
                raise APIError(resp.status_code, resp.text)
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                raise EmptyResponseError("assistant content empty")
            return content

        # Stream path: parse SSE manually so we don't pull in a heavier dep.
        out: list[str] = []
        saw_done = False
        with self._http().stream(
            "POST",
            "/v1/inference/chat/completions",
            json=body,
            headers=self._auth_headers(),
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
                    import json as _json
                    chunk = _json.loads(payload)
                except Exception:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
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
