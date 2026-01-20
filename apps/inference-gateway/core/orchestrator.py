import asyncio
import logging
import time
from typing import Any, Dict, List

from client import filtration_client
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from .providers import get_adapter, is_external_engine
from .rate_limiter import rate_limiter
from .service import GatewayService
from .stream_processor import StreamProcessor

logger = logging.getLogger(__name__)


class OrchestrationService:
    """
    Orchestrates the lifecycle of an inference request:
    Auth -> Context -> RateLimit -> Quota -> Guardrails -> Inference -> Logging
    """

    @staticmethod
    async def handle_completion(
        api_key: str, body: Dict, background_tasks: BackgroundTasks
    ):
        start_time = time.time()

        # Validation
        model = body.get("model")
        messages = body.get("messages", [])
        if not model or not messages:
            raise HTTPException(
                status_code=400, detail="Model and messages are required"
            )

        # 1. Resolve Context
        context = await GatewayService.resolve_context(api_key, model)

        deployment = context["deployment"]
        deployment_id = deployment.get("id")
        user_context_id = context["user_id_context"]
        org_id = context.get("org_id")
        guardrail_cfg = context["guardrail_config"] or {}
        rag_cfg = context["rag_config"] or {}
        template_config = context.get("template_config")
        rate_limit_config = context.get("rate_limit_config")

        # 2. Rate Limit
        if rate_limit_config and rate_limit_config.get("enabled", True):
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

        # 3. Check Quota & 4. Input Guardrails (Parallel)
        # We run these in parallel to save latency.
        quota_task = asyncio.create_task(
            filtration_client.check_quota(user_context_id, model)
        )
        scan_task = asyncio.create_task(
            GatewayService.scan_input(messages, guardrail_cfg, user_context_id)
        )

        try:
            # Wait for Quota (Gatekeeper)
            await quota_task
        except Exception:
            scan_task.cancel()
            raise

        # Wait for Input Scan (Data Modifier)
        await scan_task

        # 5. Prompt Processing (RAG / Templates)
        # Note: GatewayService.process_prompt now fails closed on error (Phase 1 Fix)
        messages = await GatewayService.process_prompt(
            messages, model, user_context_id, org_id, rag_cfg, template_config, body
        )

        # 6. Prepare Provider Request
        endpoint_url = deployment.get("endpoint")
        if not endpoint_url:
            raise HTTPException(
                status_code=500,
                detail="Deployment misconfiguration: No endpoint_url provided",
            )
        endpoint_url = endpoint_url.strip()

        # Get engine type for provider-specific routing
        engine = deployment.get("engine", "vllm")
        
        # Get provider adapter
        adapter = get_adapter(engine)
        
        # Resolve API key from credentials_json (Management API) or configuration (Orchestration API)
        credentials = deployment.get("credentials_json") or deployment.get("configuration") or {}
        provider_key = (
            credentials.get("api_key")
            or credentials.get("key")
            or credentials.get("token")
        )
        
        # Special handling for Nosana deployments - use global internal API key
        provider = deployment.get("provider", "")
        if provider == "nosana" or "nos.ci" in endpoint_url:
            from config import settings
            nosana_key = settings.nosana_internal_api_key
            if nosana_key:
                provider_key = nosana_key
                logger.debug("Using NOSANA_INTERNAL_API_KEY for Nosana deployment")

        # Use provider-specific headers
        provider_headers = adapter.get_headers(provider_key)

        provider_payload = body.copy()
        provider_payload["messages"] = messages
        
        # Resolve model name for provider API:
        # Priority: inference_model > configuration.model > model_name
        # For external providers, configuration.model contains the actual provider model name
        if deployment.get("inference_model"):
            provider_payload["model"] = deployment.get("inference_model")
        elif credentials.get("model"):
            # External providers store actual model name in configuration
            provider_payload["model"] = credentials.get("model")
        elif deployment.get("model_name"):
            provider_payload["model"] = deployment.get("model_name")

        # 7. Execute Request
        if body.get("stream"):
            return OrchestrationService._handle_streaming(
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
            )
        else:
            return await OrchestrationService._handle_standard(
                endpoint_url,
                provider_payload,
                provider_headers,
                engine,
                deployment_id,
                user_context_id,
                model,
                body,
                start_time,
                guardrail_cfg,
                background_tasks,
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
    ):
        # Tracker state
        tracker = {"prompt_tokens": 0, "completion_tokens": 0, "ttft_ms": None}

        stream_gen = GatewayService.stream_upstream(
            endpoint_url, provider_payload, provider_headers, engine
        )

        # Wrap stream with processor
        processed_stream = StreamProcessor.process_stream(
            stream_gen, start_time, tracker
        )

        # Background task wrapper to capture tracker state AFTER stream ends
        async def log_after_stream():
            # Wait for strict completion loop or handle inside generator?
            # BackgroundTasks runs after response.
            pass  # We need a way to log after stream inside the response handling or middleware
            # The previous implementation put bg tasks inside the generator finally block.

        # We need a generator that logs on finish.
        async def logging_generator_wrapper():
            try:
                async for chunk in processed_stream:
                    yield chunk
            finally:
                # Log completion
                # Log completion - Fire and forget using asyncio.create_task
                # We can't use FastAPI BackgroundTasks here because the response is already returned.
                # But we can spawn a task that outlives this generator.
                asyncio.create_task(
                    OrchestrationService._log_request(
                        deployment_id,
                        user_context_id,
                        model,
                        original_body,
                        start_time,
                        tracker["prompt_tokens"],
                        tracker["completion_tokens"],
                        tracker["ttft_ms"],
                        is_streaming=True,
                    )
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
        guardrail_cfg,
        background_tasks,
    ):
        response_data = await GatewayService.call_upstream(
            endpoint_url, provider_payload, provider_headers, engine
        )

        # Output Guardrails
        if response_data and response_data.get("choices"):
            content = response_data["choices"][0]["message"]["content"]
            await GatewayService.scan_output(
                content,
                provider_payload["messages"][-1]["content"],
                guardrail_cfg,
                user_context_id,
            )

        # Usage
        usage = response_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Log
        background_tasks.add_task(
            OrchestrationService._log_request,
            deployment_id,
            user_context_id,
            model,
            original_body,
            start_time,
            prompt_tokens,
            completion_tokens,
            None,
            False,
        )

        return response_data

    # _log_request_sync removed

    @staticmethod
    async def _log_request(
        deployment_id,
        user_id,
        model,
        request_payload,
        start_time,
        prompt_tokens,
        completion_tokens,
        ttft_ms,
        is_streaming,
    ):
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)
        total_tokens = prompt_tokens + completion_tokens

        tokens_per_second = None
        if latency_ms > 0 and completion_tokens > 0:
            tokens_per_second = round(completion_tokens / (latency_ms / 1000), 2)

        # Log to Filtration
        await filtration_client.log_inference(
            deployment_id=deployment_id,
            user_id=user_id,
            model=model,
            request_payload=request_payload,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            tokens_per_second=tokens_per_second,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status_code=200,
            is_streaming=is_streaming,
        )

        # Track Usage
        await filtration_client.track_usage(
            user_id,
            model,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        )
