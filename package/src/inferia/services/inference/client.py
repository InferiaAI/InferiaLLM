"""
HTTP client for communicating with API Gateway Service.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx
from inferia.services.inference.config import settings
from fastapi import HTTPException
from fastapi import status as http_status

logger = logging.getLogger(__name__)


class ApiGatewayClient:
    """Client for making requests to the API Gateway."""

    def __init__(self):
        self.base_url = settings.api_gateway_url
        self.internal_key = settings.api_gateway_internal_key
        self.timeout = settings.request_timeout
        self._client: Optional[httpx.AsyncClient] = None
        # Local in-memory cache for resolved contexts to reduce network hops
        import cachetools

        self.context_cache = cachetools.TTLCache(
            maxsize=settings.context_cache_maxsize, ttl=settings.context_cache_ttl
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=100, max_connections=1000
                ),
            )
        return self._client

    async def close_client(self):
        """Close the shared client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ... (skipping methods)

    def _get_headers(self, auth_token: Optional[str] = None) -> Dict[str, str]:
        """Build headers for API Gateway requests."""
        headers = {
            "X-Internal-API-Key": self.internal_key,
            "Content-Type": "application/json",
        }

        if auth_token:
            headers["Authorization"] = auth_token

        return headers

    async def check_quota(self, user_id: str, model: str) -> None:
        """
        Check if user has sufficient quota.
        """
        client = self._get_client()
        try:
            response = await client.post(
                "/internal/policy/check_quota",
                json={"user_id": user_id, "model": model},
                headers=self._get_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                detail = e.response.json().get("detail", "Quota exceeded")
                raise HTTPException(status_code=429, detail=detail)
            raise HTTPException(
                status_code=500, detail=f"Policy check failed: {str(e)}"
            )

    async def track_usage(
        self, user_id: str, model: str, usage: Dict[str, int]
    ) -> None:
        """
        Track usage asynchronously (fire and forget).
        """
        client = self._get_client()
        try:
            # We don't await the result strictly or raise specific errors for usage tracking failures
            # to avoid failing the user request if stats service is down, unless critical.
            # ideally this is a background task.
            await client.post(
                "/internal/policy/track_usage",
                json={"user_id": user_id, "model": model, "usage": usage},
                headers=self._get_headers(),
            )
        except Exception as e:
            print(f"Failed to track usage: {e}")

    async def log_inference(
        self,
        deployment_id: str,
        user_id: str,
        model: str,
        request_payload: Optional[Dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
        ttft_ms: Optional[int] = None,
        tokens_per_second: Optional[float] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        status_code: int = 200,
        error_message: Optional[str] = None,
        is_streaming: bool = False,
        applied_policies: Optional[List[str]] = None,
        ip_address: Optional[str] = None,
        request_type: str = "llm",
        input_count: Optional[int] = None,
    ) -> None:
        """
        Log inference request details (fire and forget).
        Uses asyncio.create_task to avoid blocking the response.

        Args:
            request_type: "llm" for chat completions, "embedding" for embedding requests
            input_count: Number of inputs (for embeddings - number of texts embedded)
        """
        client = self._get_client()

        # Define the task function
        async def _send_log():
            try:
                log_data = {
                    "deployment_id": deployment_id,
                    "user_id": user_id,
                    "model": model,
                    "request_payload": request_payload,
                    "latency_ms": latency_ms,
                    "ttft_ms": ttft_ms,
                    "tokens_per_second": tokens_per_second,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "status_code": status_code,
                    "error_message": error_message,
                    "is_streaming": is_streaming,
                    "applied_policies": applied_policies,
                    "ip_address": ip_address,
                    "request_type": request_type,
                }

                # Add embedding-specific fields
                if request_type == "embedding" and input_count is not None:
                    log_data["input_count"] = input_count

                await client.post(
                    "/internal/logs/create",
                    json=log_data,
                    headers=self._get_headers(),
                )
            except Exception as e:
                logger.error(f"Failed to log inference (background): {e}")

        # Fire and forget
        import asyncio

        asyncio.create_task(_send_log())

    async def login(self, username: str, password: str) -> Dict[str, Any]:
        """Proxy login request to API Gateway."""
        # Note: login uses a different base URL structure in the original code?
        # Original: url = f"{self.base_url}/auth/login"
        # Since we set base_url in the client, we should use relative paths.

        client = self._get_client()
        payload = {"username": username, "password": password}

        try:
            response = await client.post("/auth/login", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"API Gateway login failed: {e}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Authentication failed"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def get_user_info(self, auth_token: str) -> Dict[str, Any]:
        """Get user information from API Gateway."""
        client = self._get_client()
        headers = self._get_headers(auth_token)

        try:
            response = await client.get("/auth/me", headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to get user info"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def create_completion(
        self, auth_token: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Proxy completion request to API Gateway."""
        client = self._get_client()
        headers = self._get_headers(auth_token)

        try:
            response = await client.post(
                "/internal/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Completion request failed: {e.response.status_code}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Completion request failed"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def list_models(self, auth_token: str) -> Dict[str, Any]:
        """Get available models from API Gateway."""
        client = self._get_client()
        headers = self._get_headers(auth_token)

        try:
            response = await client.get("/internal/models", headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to list models"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def get_permissions(self, auth_token: str) -> Dict[str, Any]:
        """Get user permissions from API Gateway."""
        client = self._get_client()
        headers = self._get_headers(auth_token)

        try:
            response = await client.get("/auth/permissions", headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to get permissions"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def resolve_context(
        self, api_key: str, model: str, model_type: str = "inference"
    ) -> Dict[str, Any]:
        """
        Resolve deployment context and config from API Gateway.
        Cached locally for 60s.
        """
        cache_key = (api_key, model, model_type)
        if cache_key in self.context_cache:
            return self.context_cache[cache_key]

        client = self._get_client()
        headers = self._get_headers()  # Use internal key
        payload = {"api_key": api_key, "model": model, "model_type": model_type}

        try:
            response = await client.post(
                "/internal/context/resolve", json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()

            # Cache success checks
            if data.get("valid"):
                self.context_cache[cache_key] = data

            return data
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get(
                    "detail", "Failed to resolve deployment context"
                ),
            )
        except Exception as e:
            # logger.error(f"Failed context resolve: {e}")
            raise HTTPException(
                status_code=500, detail="Failed to resolve deployment context"
            )

    async def retrieve_rag_context(
        self, collection: str, query: str, top_k: int
    ) -> list:
        """
        Retrieve RAG context.
        """
        client = self._get_client()
        headers = self._get_headers()
        payload = {"collection_name": collection, "query": query, "n_results": top_k}

        try:
            response = await client.post(
                "/internal/data/retrieve", json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()
            return data.get("context", [])
        except Exception:
            return []

    async def scan_content(
        self, auth_token: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Proxy guardrail scan request to API Gateway."""
        client = self._get_client()
        headers = self._get_headers(auth_token)

        try:
            response = await client.post(
                "/internal/guardrails/scan", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Guardrail scan failed: {e.response.status_code}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Guardrail scan failed"),
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to API Gateway: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API Gateway unavailable",
            )

    async def process_prompt(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call internal prompt process endpoint.
        """
        client = self._get_client()
        headers = self._get_headers()

        try:
            response = await client.post(
                "/internal/prompt/process", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Process prompt failed: {e}")
            raise HTTPException(status_code=500, detail="Prompt processing failed")


# Global client instance
api_gateway_client = ApiGatewayClient()
