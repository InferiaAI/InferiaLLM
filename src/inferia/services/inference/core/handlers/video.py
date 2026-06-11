"""Video generation/edit/extension/status request handlers."""

import logging
import time
from typing import Dict, List, Optional

import httpx
from fastapi import BackgroundTasks, HTTPException

from inferia.services.inference.client import api_gateway_client
from inferia.services.inference.config import settings
from ..http_client import http_client
from ..pipeline import Pipeline, RequestContext
from ..providers import get_adapter, is_external_engine
from ..request_logger import RequestLogger
from ..service import GatewayService

logger = logging.getLogger(__name__)


class VideoHandler:
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
        return await VideoHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="video_generation",
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
        if not body.get("model"):
            raise HTTPException(status_code=400, detail="Model is required")
        if not body.get("video"):
            raise HTTPException(
                status_code=400,
                detail="'video' (base64) is required for video edits",
            )
        return await VideoHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="video_edit",
            extra_payload_fields=["video"],
        )

    @staticmethod
    async def handle_extension(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not body.get("model"):
            raise HTTPException(status_code=400, detail="Model is required")
        if not body.get("video"):
            raise HTTPException(
                status_code=400,
                detail="'video' (base64) is required for video extensions",
            )
        return await VideoHandler._handle(
            api_key=api_key,
            body=body,
            background_tasks=background_tasks,
            ip_address=ip_address,
            sandbox=sandbox,
            request_type="video_extension",
            extra_payload_fields=["video"],
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

        await Pipeline.resolve_context(ctx, model_type="video_generation")
        await Pipeline.check_rate_limit(ctx)
        await Pipeline.check_quota(ctx)
        Pipeline.resolve_provider(ctx, default_engine="inferia-diffusion")

        provider_payload = ctx.adapter.transform_request(body.copy())

        for field in extra_payload_fields:
            if field in body:
                provider_payload[field] = body[field]

        status_code = 200
        error_message = None
        n_videos = body.get("n", 1)

        try:
            video_path = ctx.adapter.get_endpoint_path(request_type)
            response_data = await GatewayService.call_upstream(
                ctx.endpoint_url,
                provider_payload,
                ctx.provider_headers,
                ctx.engine,
                path=video_path,
                concurrency_key=ctx.concurrency_key,
                timeout=settings.upstream_video_timeout_seconds,
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
                n_items=n_videos,
            )

    @staticmethod
    async def handle_status(
        api_key: str,
        video_id: str,
        model: str,
        sandbox: bool = False,
    ):
        """Get video generation status — single pass-through."""
        context = await GatewayService.resolve_context(
            api_key, model, model_type="video_generation", sandbox=sandbox
        )
        if not context.get("valid"):
            raise HTTPException(
                status_code=404, detail=context.get("error", "Deployment not found")
            )

        deployment = context["deployment"]
        endpoint_url = deployment.get("endpoint")
        engine = deployment.get("engine", "inferia-diffusion")

        if not endpoint_url or not endpoint_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=502,
                detail="Video endpoint URL is invalid or missing",
            )

        adapter = get_adapter(engine)

        provider_key = settings.api_gateway_internal_key or ""
        if is_external_engine(engine):
            credentials = (
                deployment.get("credentials_json")
                or deployment.get("configuration")
                or {}
            )
            provider_key = str(
                credentials.get("api_key")
                or credentials.get("key")
                or credentials.get("token")
                or ""
            )

        provider_headers = adapter.get_headers(provider_key)

        status_url = f"{endpoint_url}/generate/v1/videos/{video_id}"

        client = http_client.get_client()

        try:
            resp = await client.get(
                status_url, headers=provider_headers, timeout=30.0
            )
            resp.raise_for_status()
            status_data = resp.json()

            status = status_data.get("status", "unknown")
            if status == "failed":
                raise HTTPException(
                    status_code=500,
                    detail=status_data.get("error", "Video generation failed"),
                )

            return adapter.transform_response(status_data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"status": "processing", "id": video_id}
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error checking video status: {e}")
            raise HTTPException(status_code=502, detail=str(e))
