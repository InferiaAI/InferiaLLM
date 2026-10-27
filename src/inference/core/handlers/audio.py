"""Audio request handlers: speech-to-text and text-to-speech."""

import logging
import time
from typing import Dict, Optional

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import Response

from ..pipeline import Pipeline, RequestContext
from ..request_logger import RequestLogger
from ..service import GatewayService

logger = logging.getLogger(__name__)

MODEL_TYPE = "audio_generation"
DEFAULT_ENGINE = "speaches"
MAX_SPEECH_CHARS = 4096  # OpenAI's limit.

# /inf shares the dashboard's origin, so never pass through e.g. text/html.
_SAFE_TYPES = ("audio/", "application/json", "text/plain", "text/vtt", "application/x-subrip")


def _safe_response(resp, default_type: str) -> Response:
    ctype = resp.headers.get("content-type") or default_type
    if not ctype.lower().startswith(_SAFE_TYPES):
        ctype = "application/octet-stream"
    return Response(content=resp.content, media_type=ctype,
                    headers={"X-Content-Type-Options": "nosniff"})


class AudioHandler:
    @staticmethod
    async def handle_transcription(
        api_key: str,
        model: str,
        audio: bytes,
        filename: str,
        content_type: Optional[str],
        fields: Dict[str, str],
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not model or not audio:
            raise HTTPException(status_code=400, detail="Model and file are required")

        async def send(ctx: RequestContext):
            return await GatewayService.call_upstream_raw(
                ctx.endpoint_url,
                ctx.adapter.get_endpoint_path("transcription"),
                ctx.provider_headers,
                files={"file": (filename, audio, content_type or "application/octet-stream")},
                data={**fields, "model": ctx.body["model"]},
                concurrency_key=ctx.concurrency_key,
                resolve_names=ctx.resolve_names,
            )

        resp = await AudioHandler._handle(
            api_key, {"model": model}, "transcription", send,
            log_payload={"model": model, "file": filename, "bytes": len(audio), **fields},
            background_tasks=background_tasks, ip_address=ip_address, sandbox=sandbox,
        )
        if "json" in resp.headers.get("content-type", ""):
            data = resp.json()
            if isinstance(data, dict) and "model" in data:
                data["model"] = model
            return data
        return _safe_response(resp, "text/plain")

    @staticmethod
    async def handle_speech(
        api_key: str,
        body: Dict,
        background_tasks: BackgroundTasks,
        ip_address: Optional[str] = None,
        sandbox: bool = False,
    ):
        if not body.get("model") or not body.get("input"):
            raise HTTPException(status_code=400, detail="Model and input are required")
        if len(str(body["input"])) > MAX_SPEECH_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Input too long (max {MAX_SPEECH_CHARS} characters)",
            )

        async def send(ctx: RequestContext):
            return await GatewayService.call_upstream_raw(
                ctx.endpoint_url,
                ctx.adapter.get_endpoint_path("speech"),
                ctx.provider_headers,
                json_body=ctx.body,
                concurrency_key=ctx.concurrency_key,
                resolve_names=ctx.resolve_names,
            )

        resp = await AudioHandler._handle(
            api_key, body, "speech", send, log_payload=body,
            background_tasks=background_tasks, ip_address=ip_address, sandbox=sandbox,
        )
        return _safe_response(resp, "audio/mpeg")

    @staticmethod
    async def _handle(
        api_key, body, request_type, send, *, log_payload,
        background_tasks, ip_address, sandbox,
    ):
        requested_model = body["model"]
        ctx = RequestContext(
            api_key=api_key,
            body=dict(body),
            model=requested_model,
            sandbox=sandbox,
            ip_address=ip_address,
            background_tasks=background_tasks,
            start_time=time.time(),
        )
        await Pipeline.resolve_context(ctx, model_type=MODEL_TYPE)
        await Pipeline.check_rate_limit(ctx)
        await Pipeline.check_quota(ctx)
        Pipeline.resolve_provider(ctx, default_engine=DEFAULT_ENGINE)

        status_code = 200
        error_message = None
        try:
            return await send(ctx)
        except HTTPException as e:
            status_code = e.status_code
            error_message = str(e.detail)
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
                org_id=ctx.org_id,
                model=requested_model,
                request_payload=log_payload,
                start_time=ctx.start_time,
                request_type=request_type,
                applied_policies=ctx.applied_policies,
                log_payloads=ctx.log_payloads,
                ip_address=ip_address,
                status_code=status_code,
                error_message=error_message,
            )
