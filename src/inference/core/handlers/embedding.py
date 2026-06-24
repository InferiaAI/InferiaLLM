"""Embedding request handler."""

import logging
import time
from typing import Dict, Optional

from fastapi import BackgroundTasks, HTTPException

from ..pipeline import Pipeline, RequestContext
from ..request_logger import RequestLogger
from ..service import GatewayService

logger = logging.getLogger(__name__)


class EmbeddingHandler:
    @staticmethod
    async def handle(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        ctx = RequestContext(
            api_key=api_key,
            body=body,
            model=body.get("model", ""),
            sandbox=sandbox,
            ip_address=ip_address,
            background_tasks=background_tasks,
            start_time=time.time(),
        )

        # Validation
        input_data = body.get("input")
        if not ctx.model or not input_data:
            raise HTTPException(status_code=400, detail="Model and input are required")

        # Pipeline
        await Pipeline.resolve_context(ctx, model_type="embedding")
        await Pipeline.check_rate_limit(ctx)
        await Pipeline.check_quota(ctx)
        Pipeline.resolve_provider(ctx, default_engine="infinity")

        provider_payload = ctx.adapter.transform_request(body.copy())

        status_code = 200
        error_message = None
        prompt_tokens = 0
        input_count = len(input_data) if isinstance(input_data, list) else 1

        try:
            embedding_path = ctx.adapter.get_endpoint_path("embedding")
            response_data = await GatewayService.call_upstream(
                ctx.endpoint_url,
                provider_payload,
                ctx.provider_headers,
                ctx.engine,
                path=embedding_path,
                concurrency_key=ctx.concurrency_key,
            )

            usage = response_data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)

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
                deployment_id=ctx.deployment_id,
                user_id=ctx.user_context_id,
                model=ctx.model,
                request_payload=body,
                start_time=ctx.start_time,
                request_type="embedding",
                applied_policies=ctx.applied_policies,
                log_payloads=ctx.log_payloads,
                ip_address=ip_address,
                status_code=status_code,
                error_message=error_message,
                prompt_tokens=prompt_tokens,
                input_count=input_count,
            )
