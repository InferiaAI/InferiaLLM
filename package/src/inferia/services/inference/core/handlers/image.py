"""Image generation/edit/variations request handlers."""

import logging
import time
from typing import Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException

from inferia.services.inference.config import settings
from ..pipeline import Pipeline, RequestContext
from ..providers import get_adapter, is_external_engine
from ..request_logger import RequestLogger
from ..service import GatewayService

logger = logging.getLogger(__name__)


class ImageHandler:
    @staticmethod
    async def handle_generation(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not body.get("model") or not body.get("prompt"):
            raise HTTPException(status_code=400, detail="Model and prompt are required")
        return await ImageHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="image_generation",
            default_engine="localai",
            extra_payload_fields=[],
        )

    @staticmethod
    async def handle_edit(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not body.get("model") or not body.get("prompt"):
            raise HTTPException(status_code=400, detail="Model and prompt are required")
        return await ImageHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="image_edit",
            default_engine="inferia-diffusion",
            extra_payload_fields=["image", "mask"],
        )

    @staticmethod
    async def handle_variations(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not body.get("model"):
            raise HTTPException(status_code=400, detail="Model is required")
        if not body.get("image"):
            raise HTTPException(status_code=400, detail="Image is required")
        return await ImageHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="image_variations",
            default_engine="inferia-diffusion",
            extra_payload_fields=["image"],
        )

    @staticmethod
    async def _handle(
        *,
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str],
        sandbox: bool,
        request_type: str,
        default_engine: str,
        extra_payload_fields: List[str],
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

        await Pipeline.resolve_context(ctx, model_type="image_generation")
        await Pipeline.check_rate_limit(ctx)
        await Pipeline.check_quota(ctx)
        Pipeline.resolve_provider(ctx, default_engine=default_engine)

        provider_payload = ctx.adapter.transform_request(body.copy())

        # Add extra fields that transform_request may not handle
        for field in extra_payload_fields:
            if field in body:
                provider_payload[field] = body[field]

        status_code = 200
        error_message = None
        n_images = body.get("n", 1)

        try:
            image_path = ctx.adapter.get_endpoint_path(request_type)
            response_data = await GatewayService.call_upstream(
                ctx.endpoint_url,
                provider_payload,
                ctx.provider_headers,
                ctx.engine,
                path=image_path,
                concurrency_key=ctx.concurrency_key,
            )

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
                request_type=request_type,
                applied_policies=ctx.applied_policies,
                log_payloads=ctx.log_payloads,
                ip_address=ip_address,
                status_code=status_code,
                error_message=error_message,
                n_items=n_images,
            )
