from fastapi import HTTPException
from inference.client import api_gateway_client
from inference.config import settings
from common.circuit_breaker import circuit_breaker_registry
from typing import Dict, Any, List, AsyncGenerator
import logging
import httpx
from .http_client import http_client
from .providers import get_adapter
from .concurrency_limiter import upstream_concurrency_limiter
from .validators import validate_upstream_url, sanitize_headers, check_response_size

logger = logging.getLogger(__name__)


class GatewayService:
    @staticmethod
    async def resolve_context(
        api_key: str, model: str, model_type: str = "inference", sandbox: bool = False
    ) -> Dict[str, Any]:
        """Resolves deployment context via Filtration Gateway."""
        context = await api_gateway_client.resolve_context(
            api_key, model, model_type, sandbox
        )

        if not context.get("valid"):
            raise HTTPException(
                status_code=401, detail=context.get("error", "Unauthorized")
            )

        return context

    @staticmethod
    def _build_full_url(endpoint_url: str, chat_path: str) -> str:
        """
        Build the full URL, handling cases where endpoint already contains part of the path.
        Prevents duplicate paths like /v1/v1/chat/completions
        """
        # Strip trailing slashes from endpoint
        endpoint = endpoint_url.rstrip("/")

        # If endpoint already contains the full path, use it as-is
        if (
            endpoint.endswith("/chat/completions")
            or endpoint.endswith("/messages")
            or endpoint.endswith("/embeddings")
            or endpoint.endswith("/images/generations")
            or endpoint.endswith("/images/edits")
            or endpoint.endswith("/images/variations")
            or endpoint.endswith("/videos/generations")
            or endpoint.endswith("/videos/edits")
            or endpoint.endswith("/videos/extensions")
        ):
            return endpoint

        # If endpoint already contains /v1 or /openai (e.g. Groq, Cerebras, Gemini),
        # strip /v1 from the path to avoid duplication
        if endpoint.endswith("/v1") or endpoint.endswith("/openai"):
            if chat_path.startswith("/v1"):
                return endpoint + chat_path[3:]  # Skip the /v1 part
            return endpoint + chat_path

        # If endpoint already contains /generate (for InferaDiffusion video endpoints)
        # Video paths already include /generate, so we need to strip it to avoid duplication
        if endpoint.endswith("/generate"):
            if chat_path.startswith("/generate"):
                return endpoint + chat_path[9:]  # Skip /generate
            return endpoint + chat_path

        # Standard case - just append the path
        return endpoint + chat_path

    @staticmethod
    def _get_allowed_hosts() -> list[str]:
        """Parse allowed internal hosts from config."""
        raw = settings.upstream_allowed_internal_hosts
        if not raw:
            return []
        return [h.strip() for h in raw.split(",") if h.strip()]

    @staticmethod
    async def stream_upstream(
        endpoint_url: str,
        payload: Dict,
        headers: Dict,
        engine: str = "vllm",
        concurrency_key: str = "default",
    ) -> AsyncGenerator[bytes, None]:
        adapter = get_adapter(engine)
        chat_path = adapter.get_chat_path()
        transformed_payload = adapter.transform_request(payload)
        full_url = GatewayService._build_full_url(endpoint_url, chat_path)

        # Validate URL and headers before making upstream request
        try:
            full_url = validate_upstream_url(
                full_url, GatewayService._get_allowed_hosts()
            )
            headers = sanitize_headers(headers)
        except ValueError as e:
            logger.error(f"Upstream validation failed: {e}")
            yield b'data: {"error": "Invalid upstream configuration"}\n\n'
            return

        breaker = circuit_breaker_registry.get_or_create(
            f"upstream:{concurrency_key}",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=(httpx.HTTPStatusError, httpx.RequestError),
        )

        if not await breaker._can_execute():
            yield b'data: {"error": "Upstream temporarily unavailable (circuit breaker open)"}\n\n'
            return

        max_bytes = settings.upstream_max_response_bytes
        client = http_client.get_client()
        try:
            # Acquire concurrency slot only for connection establishment,
            # then release it so long-running streams don't cause
            # head-of-line blocking for new requests.
            async with upstream_concurrency_limiter.limit(concurrency_key):
                request = client.build_request(
                    "POST", full_url, json=transformed_payload, headers=headers
                )
                response = await client.send(request, stream=True)
                response.raise_for_status()
            # Slot released — stream body without holding it
            try:
                total_bytes = 0
                async for chunk in response.aiter_raw():
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        yield b'data: {"error": "Upstream response exceeded size limit"}\n\n'
                        return
                    yield chunk
            finally:
                await response.aclose()
        except httpx.HTTPStatusError as e:
            await breaker._record_failure()
            logger.error(f"Upstream Error {e.response.status_code}: {e.response.text}")
            yield b'data: {"error": "Upstream provider returned an error"}\n\n'
        except Exception as e:
            await breaker._record_failure()
            logger.error(f"Streaming Exception: {e}")
            yield b'data: {"error": "Streaming connection failed"}\n\n'

    @staticmethod
    async def call_upstream(
        endpoint_url: str,
        payload: Dict,
        headers: Dict,
        engine: str = "vllm",
        path: str = None,
        concurrency_key: str = "default",
        timeout: float = None,
        transform_response: bool = True,
    ) -> Dict:
        adapter = get_adapter(engine)
        # Use custom path if provided, otherwise use adapter's chat path
        if path:
            full_url = GatewayService._build_full_url(endpoint_url, path)
        else:
            chat_path = adapter.get_chat_path()
            full_url = GatewayService._build_full_url(endpoint_url, chat_path)

        # Validate URL and headers before making upstream request
        try:
            full_url = validate_upstream_url(
                full_url, GatewayService._get_allowed_hosts()
            )
            headers = sanitize_headers(headers)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        transformed_payload = adapter.transform_request(payload)
        max_bytes = settings.upstream_max_response_bytes

        breaker = circuit_breaker_registry.get_or_create(
            f"upstream:{concurrency_key}",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=(httpx.HTTPStatusError, httpx.RequestError),
        )

        if not await breaker._can_execute():
            raise HTTPException(
                status_code=503,
                detail="Upstream temporarily unavailable (circuit breaker open)",
            )

        client = http_client.get_client()
        request_timeout = timeout or settings.upstream_http_timeout_seconds
        try:
            async with upstream_concurrency_limiter.limit(concurrency_key):
                resp = await client.post(
                    full_url,
                    json=transformed_payload,
                    headers=headers,
                    timeout=request_timeout,
                )
                resp.raise_for_status()

                # Check response size before parsing
                content_length = resp.headers.get("content-length")
                if content_length:
                    check_response_size(int(content_length), max_bytes)
                body = resp.content
                if len(body) > max_bytes:
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream response exceeded size limit",
                    )
                raw_response = resp.json()

                await breaker._record_success()
                if transform_response:
                    return adapter.transform_response(raw_response)
                return raw_response
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            await breaker._record_failure()
            logger.error(f"Provider error {e.response.status_code}: {e.response.text}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail="Upstream provider returned an error",
            )
        except Exception as e:
            await breaker._record_failure()
            logger.error(f"Provider request failed: {e}")
            raise HTTPException(
                status_code=502, detail="Upstream provider is unavailable"
            )
