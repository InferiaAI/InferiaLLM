"""
Reusable pipeline steps for inference request handling.

Extracts the duplicated context resolution, rate limiting, quota checking,
and provider key resolution into a single Pipeline class.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, HTTPException

from services.inference.client import api_gateway_client
from services.inference.config import settings
from .providers import ProviderAdapter, get_adapter, is_external_engine, resolve_upstream
from .rate_limiter import rate_limiter
from .service import GatewayService

logger = logging.getLogger(__name__)


@dataclass
class RequestContext:
    """Carries resolved state through the inference pipeline."""

    # --- Input (set by caller) ---
    api_key: str = ""
    body: Dict = field(default_factory=dict)
    model: str = ""
    sandbox: bool = False
    ip_address: Optional[str] = None
    background_tasks: Optional[BackgroundTasks] = None
    start_time: float = field(default_factory=time.time)

    # --- Populated by Pipeline.resolve_context ---
    deployment: Dict = field(default_factory=dict)
    deployment_id: Optional[str] = None
    concurrency_key: str = ""
    user_context_id: str = ""
    org_id: Optional[str] = None
    rate_limit_config: Optional[Dict] = None
    log_payloads: bool = True

    # --- Populated by Pipeline.resolve_provider ---
    adapter: Optional[ProviderAdapter] = None
    engine: str = ""
    endpoint_url: str = ""
    provider_key: str = ""
    provider_headers: Dict = field(default_factory=dict)

    # --- Tracking ---
    applied_policies: List[str] = field(default_factory=list)
    status_code: int = 200
    error_message: Optional[str] = None


class Pipeline:
    """Static methods implementing each reusable pipeline step."""

    @staticmethod
    async def resolve_context(ctx: RequestContext, model_type: str = "inference") -> None:
        """Resolve deployment context via the API gateway."""
        context = await GatewayService.resolve_context(
            ctx.api_key, ctx.model, model_type=model_type, sandbox=ctx.sandbox
        )

        deployment = context["deployment"]
        ctx.deployment = deployment
        ctx.deployment_id = deployment.get("id")
        ctx.concurrency_key = str(ctx.deployment_id or ctx.model)
        ctx.user_context_id = context["user_id_context"]
        ctx.org_id = context.get("org_id")
        ctx.rate_limit_config = context.get("rate_limit_config")
        ctx.log_payloads = context.get("log_payloads", True)

    @staticmethod
    async def check_rate_limit(ctx: RequestContext) -> None:
        """Check token-bucket rate limit for the deployment."""
        rate_limit_config = ctx.rate_limit_config
        if not rate_limit_config or not rate_limit_config.get("enabled", True):
            return

        ctx.applied_policies.append("rate_limit")
        rpm = int(rate_limit_config.get("rpm", 0))
        if rpm > 0:
            allowed, wait_time = rate_limiter.check_limit(
                f"deployment:{ctx.deployment_id}", rpm
            )
            if not allowed:
                headers = {"Retry-After": str(int(wait_time) + 1)}
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Limit: {rpm} RPM.",
                    headers=headers,
                )

    @staticmethod
    async def check_quota(ctx: RequestContext) -> None:
        """Check usage quota for the user/model."""
        ctx.applied_policies.append("quota")
        await api_gateway_client.check_quota(ctx.user_context_id, ctx.model)

    @staticmethod
    def resolve_provider(ctx: RequestContext, default_engine: str = "vllm") -> None:
        """Resolve adapter, provider key, headers, and model name override.

        Fixes the double model-override bug in the original code by applying
        model name resolution exactly once.
        """
        deployment = ctx.deployment
        engine = deployment.get("engine", default_engine)
        ctx.engine = engine
        raw_endpoint = deployment.get("endpoint", "")
        ctx.adapter, ctx.endpoint_url = resolve_upstream(
            engine, raw_endpoint, settings.external_proxy_url,
        )

        # Resolve API key and model name from credentials
        if is_external_engine(engine):
            credentials = (
                deployment.get("credentials_json")
                or deployment.get("configuration")
                or {}
            )
            ctx.provider_key = str(
                credentials.get("api_key")
                or credentials.get("key")
                or credentials.get("token")
                or ""
            )
            # Resolve model name: inference_model > credentials.model > original
            if deployment.get("inference_model"):
                ctx.body["model"] = deployment["inference_model"]
            elif credentials.get("model"):
                ctx.body["model"] = credentials["model"]
        else:
            ctx.provider_key = settings.api_gateway_internal_key or ""
            # For compute engines, override model name if specified
            if deployment.get("inference_model"):
                ctx.body["model"] = deployment["inference_model"]

        ctx.provider_headers = ctx.adapter.get_headers(ctx.provider_key)
