"""Chat completion request handler (streaming + standard)."""

import asyncio
import logging
import time
from typing import Dict, Optional

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from inferia.services.inference.client import api_gateway_client
from inferia.services.inference.config import settings
from ..pipeline import Pipeline, RequestContext
from ..providers import get_adapter, is_external_engine
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
        guardrail_cfg = context["guardrail_config"] or {}
        rag_cfg = context["rag_config"] or {}
        template_config = context.get("template_config")
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
        quota_task = asyncio.create_task(
            api_gateway_client.check_quota(user_context_id, model)
        )

        # 4. Input Guardrails
        scan_task = None
        if guardrail_cfg.get("enabled") or guardrail_cfg.get("pii_enabled"):
            applied_policies.append("guardrail")
            if guardrail_cfg.get("pii_enabled"):
                applied_policies.append("pii")
            scan_task = asyncio.create_task(
                GatewayService.scan_input(messages, guardrail_cfg, user_context_id)
            )

        try:
            await quota_task
        except Exception:
            if scan_task is not None:
                scan_task.cancel()
            raise

        if scan_task is not None:
            await scan_task

        # 5. Prompt Processing (RAG / Templates)
        if rag_cfg.get("enabled"):
            applied_policies.append("rag")
        if template_config and template_config.get("enabled"):
            applied_policies.append("prompt_template")

        messages = await GatewayService.process_prompt(
            messages,
            model,
            user_context_id,
            org_id or "default",
            rag_cfg,
            template_config or {},
            body,
        )

        # 6. Prepare Provider Request
        endpoint_url = deployment.get("endpoint")
        if not endpoint_url:
            raise HTTPException(
                status_code=500,
                detail="Deployment misconfiguration: No endpoint_url provided",
            )
        endpoint_url = endpoint_url.strip()

        engine = deployment.get("engine", "vllm")
        adapter = get_adapter(engine)

        credentials = (
            deployment.get("credentials_json") or deployment.get("configuration") or {}
        )
        provider_key = str(
            credentials.get("api_key")
            or credentials.get("key")
            or credentials.get("token")
            or ""
        )

        if not provider_key and not is_external_engine(engine):
            provider_key = settings.api_gateway_internal_key

        provider_headers = adapter.get_headers(provider_key)

        provider_payload = body.copy()
        provider_payload["messages"] = messages

        if deployment.get("inference_model"):
            provider_payload["model"] = deployment.get("inference_model")
        elif credentials.get("model"):
            provider_payload["model"] = credentials.get("model")
        elif deployment.get("model_name"):
            provider_payload["model"] = deployment.get("model_name")

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
                guardrail_cfg,
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
                    asyncio.create_task(
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
        guardrail_cfg,
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

            # Output Guardrails
            if response_data and response_data.get("choices"):
                content = (response_data.get("choices") or [{}])[0].get(
                    "message", {}
                ).get("content") or ""
                last_msg = provider_payload.get("messages", [{}])[-1]
                input_content = (
                    last_msg.get("content", "") if isinstance(last_msg, dict) else ""
                )
                if content and input_content:
                    await GatewayService.scan_output(
                        content,
                        input_content,
                        guardrail_cfg,
                        user_context_id,
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
