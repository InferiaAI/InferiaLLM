"""Chat completion request handler (streaming + standard)."""

import asyncio
import logging
import time
from typing import Dict, Optional, Set

# Prevent fire-and-forget log tasks from being GC'd before completion
_background_log_tasks: Set[asyncio.Task] = set()

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from inference.client import api_gateway_client
from inference.config import settings
from ..providers import resolve_upstream
from ..worker_routing import (
    echo_requested_model,
    envoy_route_headers,
    provider_auth,
    upstream_model,
)
from ..rate_limiter import rate_limiter
from ..request_logger import RequestLogger
from ..service import GatewayService
from ..stream_processor import StreamProcessor

logger = logging.getLogger(__name__)


class CompletionHandler:
    @staticmethod
    async def handle(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        start_time = time.time()
        applied_policies = []

        # Validation
        model = body.get("model")
        messages = body.get("messages", [])
        if not model or not messages:
            raise HTTPException(
                status_code=400, detail="Model and messages are required"
            )

        # 1. Resolve Context
        context = await GatewayService.resolve_context(api_key, model, sandbox=sandbox)

        deployment = context["deployment"]
        deployment_id = deployment.get("id")
        concurrency_key = str(deployment_id or model)
        user_context_id = context["user_id_context"]
        org_id = context.get("org_id")
        rate_limit_config = context.get("rate_limit_config")
        log_payloads = context.get("log_payloads", True)

        # 2. Rate Limit
        if rate_limit_config and rate_limit_config.get("enabled", True):
            applied_policies.append("rate_limit")
            rpm = int(rate_limit_config.get("rpm", 0))
            if rpm > 0:
                allowed, wait_time = rate_limiter.check_limit(
                    f"deployment:{deployment_id}", rpm
                )
                if not allowed:
                    headers = {"Retry-After": str(int(wait_time) + 1)}
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded. Limit: {rpm} RPM.",
                        headers=headers,
                    )

        # 3. Check Quota
        applied_policies.append("quota")
        await api_gateway_client.check_quota(user_context_id, model)

        # 4. Prepare Provider Request
        endpoint_url = deployment.get("endpoint")
        if not endpoint_url:
            raise HTTPException(
                status_code=500,
                detail="Deployment misconfiguration: No endpoint_url provided",
            )
        endpoint_url = endpoint_url.strip()

        engine = deployment.get("engine", "vllm")
        adapter, endpoint_url = resolve_upstream(
            engine, endpoint_url, settings.external_proxy_url,
        )

        # Resolve upstream auth + routing. Worker-hosted deploys (a pool
        # inference_token is present in the resolved context) auth to the
        # worker's :8080 proxy with that token and must carry the
        # X-Inferia-Deployment-Id header so the worker routes to the right
        # model container; external providers keep their own api_key.
        provider_key, extra_headers = provider_auth(deployment, engine)
        provider_headers = adapter.get_headers(provider_key)
        provider_headers.update(extra_headers)

        # --- Envoy proxy routing ---
        # When ENVOY_URL is configured and the deployment is worker-hosted,
        # route through the front Envoy instead of directly to the worker.
        # The X-Inferia-Route-Cluster header tells Envoy which upstream
        # cluster to forward to (grouped by pool_id or individual node).
        _envoy_url, _envoy_headers = envoy_route_headers(
            deployment, settings.envoy_url,
        )
        if _envoy_url:
            endpoint_url = _envoy_url
            provider_headers.update(_envoy_headers)

        provider_payload = body.copy()
        provider_payload["messages"] = messages

        # Send the real upstream model id (e.g. the ollama tag gemma3:4b),
        # never the human display name the sandbox sent.
        resolved_model = upstream_model(deployment)
        if resolved_model:
            provider_payload["model"] = resolved_model

        # 7. Execute Request
        if body.get("stream"):
            return CompletionHandler._handle_streaming(
                endpoint_url,
                provider_payload,
                provider_headers,
                engine,
                adapter,
                deployment_id,
                user_context_id,
                model,
                body,
                start_time,
                background_tasks,
                applied_policies,
                log_payloads,
                ip_address,
                concurrency_key,
                org_id=org_id,
            )
        else:
            return await CompletionHandler._handle_standard(
                endpoint_url,
                provider_payload,
                provider_headers,
                engine,
                deployment_id,
                user_context_id,
                model,
                body,
                start_time,
                background_tasks,
                applied_policies,
                log_payloads,
                ip_address,
                concurrency_key,
                org_id=org_id,
            )

    @staticmethod
    def _handle_streaming(
        endpoint_url,
        provider_payload,
        provider_headers,
        engine,
        adapter,
        deployment_id,
        user_context_id,
        model,
        original_body,
        start_time,
        background_tasks,
        applied_policies,
        log_payloads,
        ip_address,
        concurrency_key,
        org_id=None,
    ):
        tokenizer_model = provider_payload.get("model") or model
        messages = provider_payload.get("messages", [])
        prompt_tokens = StreamProcessor.estimate_prompt_tokens(
            messages, tokenizer_model
        )

        tracker = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 0,
            "ttft_ms": None,
            "_tokenizer_model": tokenizer_model,
        }

        # Without this the counts are an estimate over the message text, which
        # misses the chat template. Quota reads these numbers.
        asked_for_usage = False
        if provider_payload.get("stream") and not provider_payload.get(
            "stream_options"
        ):
            provider_payload["stream_options"] = {"include_usage": True}
            asked_for_usage = True

        upstream_error: Dict = {}
        stream_gen = GatewayService.stream_upstream(
            endpoint_url,
            provider_payload,
            provider_headers,
            engine,
            concurrency_key=concurrency_key,
            error_sink=upstream_error,
        )

        processed_stream = StreamProcessor.process_stream(
            stream_gen,
            start_time,
            tracker,
            rewrite_model=model,
            adapter=adapter,
            drop_usage_chunk=asked_for_usage,
        )

        async def logging_generator_wrapper():
            status_code = 200
            error_message = None
            try:
                async for chunk in processed_stream:
                    yield chunk
            except HTTPException as e:
                status_code = e.status_code
                error_message = str(e.detail) if hasattr(e, "detail") else str(e)
                raise
            except Exception as e:
                status_code = 500
                error_message = str(e)
                raise
            finally:
                # An upstream failure never raises, so nothing above sees it.
                if status_code == 200 and upstream_error:
                    status_code = upstream_error.get("status_code", 502)
                    error_message = upstream_error.get("message")
                try:
                    task = asyncio.create_task(
                        RequestLogger.log(
                            deployment_id=deployment_id,
                            user_id=user_context_id,
                            org_id=org_id,
                            model=model,
                            request_payload=original_body,
                            start_time=start_time,
                            request_type="llm",
                            applied_policies=applied_policies,
                            log_payloads=log_payloads,
                            ip_address=ip_address,
                            status_code=status_code,
                            error_message=error_message,
                            prompt_tokens=tracker["prompt_tokens"],
                            completion_tokens=tracker["completion_tokens"],
                            ttft_ms=tracker["ttft_ms"],
                            is_streaming=True,
                        )
                    )
                    # Hold a strong reference so the task isn't GC'd before completion
                    _background_log_tasks.add(task)
                    task.add_done_callback(_background_log_tasks.discard)
                except RuntimeError:
                    logger.error(
                        "Failed to schedule streaming log task: no running event loop"
                    )

        return StreamingResponse(
            logging_generator_wrapper(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @staticmethod
    async def _handle_standard(
        endpoint_url,
        provider_payload,
        provider_headers,
        engine,
        deployment_id,
        user_context_id,
        model,
        original_body,
        start_time,
        background_tasks,
        applied_policies,
        log_payloads,
        ip_address,
        concurrency_key,
        org_id=None,
    ):
        status_code = 200
        error_message = None
        prompt_tokens = 0
        completion_tokens = 0

        try:
            response_data = await GatewayService.call_upstream(
                endpoint_url,
                provider_payload,
                provider_headers,
                engine,
                concurrency_key=concurrency_key,
            )

            usage = response_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            return echo_requested_model(response_data, model)
        except HTTPException as e:
            status_code = e.status_code
            error_message = str(e.detail) if hasattr(e, "detail") else str(e)
            raise
        except Exception as e:
            status_code = 500
            error_message = str(e)
            raise
        finally:
            background_tasks.add_task(
                RequestLogger.log,
                deployment_id=deployment_id,
                user_id=user_context_id,
                org_id=org_id,
                model=model,
                request_payload=original_body,
                start_time=start_time,
                request_type="llm",
                applied_policies=applied_policies,
                log_payloads=log_payloads,
                ip_address=ip_address,
                status_code=status_code,
                error_message=error_message,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                is_streaming=False,
            )
