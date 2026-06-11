"""Chat completion request handler (streaming + standard)."""

import asyncio
import logging
import time
from typing import Dict, Optional, Set

# Prevent fire-and-forget log tasks from being GC'd before completion
_background_log_tasks: Set[asyncio.Task] = set()

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from services.inference.client import api_gateway_client
from services.inference.config import settings
from ..pipeline import Pipeline, RequestContext
from ..providers import get_adapter, resolve_upstream
from ..worker_routing import provider_auth, upstream_model
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
        provider_key, extra_headers = provider_auth(
            deployment, engine, settings.api_gateway_internal_key,
        )
        provider_headers = adapter.get_headers(provider_key)
        provider_headers.update(extra_headers)

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
            )

    @staticmethod
    def _handle_streaming(
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

        stream_gen = GatewayService.stream_upstream(
            endpoint_url,
            provider_payload,
            provider_headers,
            engine,
            concurrency_key=concurrency_key,
        )

        processed_stream = StreamProcessor.process_stream(
            stream_gen, start_time, tracker
        )

        async def logging_generator_wrapper():
            status_code = 200
            error_message = None
            try:
                async for chunk in processed_stream:
                    if b'"error":' in chunk and b"Upstream Error" in chunk:
                        status_code = 502
                        error_message = chunk.decode("utf-8", errors="ignore")
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
                try:
                    task = asyncio.create_task(
                        RequestLogger.log(
                            deployment_id=deployment_id,
                            user_id=user_context_id,
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

            return response_data
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
