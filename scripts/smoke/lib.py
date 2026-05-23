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
