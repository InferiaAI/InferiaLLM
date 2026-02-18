import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from inferia.services.api_gateway.audit.service import audit_service
from inferia.services.api_gateway.models import AuditLogCreate

import cachetools
from inferia.services.api_gateway.db.models import Usage as DBUsage
from fastapi import HTTPException, status
from inferia.services.api_gateway.models import UserContext
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# Hardcoded Limits for MVP (TODO: Move to Policy/Plan configuration)
DEFAULT_DAILY_REQUEST_LIMIT = 1000
DEFAULT_DAILY_TOKEN_LIMIT = 100000

from typing import Any

from inferia.services.api_gateway.db.models import ApiKey as DBApiKey
from inferia.services.api_gateway.db.models import Deployment as DBDeployment
from inferia.services.api_gateway.db.models import Policy as DBPolicy
from inferia.services.api_gateway.db.models import Organization as DBOrganization
from inferia.services.api_gateway.rbac.auth import auth_service


import redis.asyncio as redis
from inferia.services.api_gateway.config import settings

# Fix import path for common module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from inferia.common.circuit_breaker import circuit_breaker, CircuitBreakerError

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


class PolicyEngine:
    def __init__(self):
        # Cache for resolve_context: Key=(api_key, model), Value=ResultDict
        # TTL=10 seconds to ensure reasonably fresh configs/keys while offloading DB
        # Reduced from 60s to handle real-time setting changes better
        self.context_cache = cachetools.TTLCache(maxsize=2000, ttl=10)

        # Cache for Org ID lookups from API Key ID: Key=api_key_id, Value=org_id
        self.org_id_cache = cachetools.TTLCache(maxsize=5000, ttl=300)

        # Cache for Quota Policies: Key=org_id, Value=PolicyConfigDict
        self.quota_policy_cache = cachetools.TTLCache(maxsize=2000, ttl=60)

        # Redis Client for Quotas
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def verify_api_key(
        self, db: AsyncSession, api_key: str
    ) -> Optional[DBApiKey]:
        """
        Verify if an API key is valid. Returns the key record if valid, None otherwise.
        """
        if not api_key:
            return None

        # Optimistic lookup by prefix
        prefix = api_key[:6] + "..."
        stmt = select(DBApiKey).where(DBApiKey.prefix == prefix)
        result = await db.execute(stmt)
        candidates = result.scalars().all()

        for key_record in candidates:
            if auth_service.verify_password(api_key, key_record.key_hash):
                return key_record

        return None

    async def resolve_context(
        self, db: AsyncSession, api_key: str, model: str
    ) -> Dict[str, Any]:
        """
        Resolve complete inference context (Deployment + Policies) from an API Key.
        """
        cache_key = (api_key, model)
        if cache_key in self.context_cache:
            return self.context_cache[cache_key]

        # 1. Verify API Key
        auth_key_record = await self.verify_api_key(db, api_key)

        if not auth_key_record:
            return {"valid": False, "error": "Invalid API Key"}

        # 2. Resolve Deployment and Organization
        stmt = select(DBDeployment, DBOrganization).join(
            DBOrganization, DBDeployment.org_id == DBOrganization.id
        )

        if auth_key_record.deployment_id:
            stmt = stmt.where(DBDeployment.id == auth_key_record.deployment_id)
        else:
            # Org scoped lookup
            stmt = stmt.where(
                (DBDeployment.org_id == auth_key_record.org_id)
                & (
                    (DBDeployment.model_name == model)
                    | (DBDeployment.llmd_resource_name == model)
                )
            )

        result = await db.execute(stmt)
        row = result.first()

        if not row:
            return {
                "valid": False,
                "error": "Deployment or Organization not found for model/key combination",
            }

        deployment, organization = row

        # 2.1 Validate Deployment State (Only allow active models for inference)
        if deployment.state not in ["RUNNING", "READY", "ready"]:
            return {
                "valid": False,
                "error": f"Model '{model}' is currently {deployment.state}. Please start the deployment first.",
            }

        # Convert deployment to dict for caching and session independence
        deployment_dict = {
            "id": deployment.id,
            "model_name": deployment.model_name,
            "endpoint": deployment.endpoint,
            "engine": deployment.engine,
            "configuration": deployment.configuration,
            "org_id": deployment.org_id,
            "policies": deployment.policies,
            "inference_model": deployment.inference_model,
        }

        # 3. Fetch Policies (DB + Deployment Metadata)
        try:
            stmt = select(DBPolicy).where(
                (DBPolicy.org_id == auth_key_record.org_id)
                & (
                    (DBPolicy.deployment_id == deployment.id)
                    | (DBPolicy.deployment_id.is_(None))
                )
            )
            result = await db.execute(stmt)
            policies = result.scalars().all()

            config = {
                "guardrail": {"enabled": False},
                "rag": {"enabled": False},
                "prompt_template": None,
            }

            # --- MERGE LOGIC ---
            # 1. Org Policies (DB)
            # 2. Deployment Policies (DB - overrides Org)
            # 3. Deployment Inline Policies (Metadata - overrides DB)

            org_policies = [p for p in policies if not p.deployment_id]
            dep_policies = [p for p in policies if p.deployment_id]

            # Prepare template definitions lookup (ID -> Content)
            template_definitions = {}
            for p in org_policies:
                if p.policy_type == "prompt_template" and p.config_json:
                    t_id = p.config_json.get("id")
                    if t_id:
                        template_definitions[t_id] = p.config_json.get("content")

            sorted_policies = org_policies + dep_policies

            # Helper to merge config
            def apply_policy(p_type, p_config):
                p_type = p_type.lower()
                if p_type in config and p_config:
                    policy_cfg = p_config.copy()
                    if "enabled" not in policy_cfg:
                        policy_cfg["enabled"] = True

                    if p_type == "prompt_engine":
                        pass
                    else:
                        config[p_type] = policy_cfg

                    # Inject content for templates
                    if p_type == "prompt_template":
                        base_id = p_config.get("base_template_id")
                        if base_id and not p_config.get("content"):
                            found_content = template_definitions.get(base_id)
                            if found_content:
                                config[p_type]["content"] = found_content

            # Apply DB Policies
            for p in sorted_policies:
                apply_policy(p.policy_type, p.config_json)

            # Apply Inline Policies (from Deployment Metadata)
            if deployment.policies:
                # Expecting dict like {"guardrail": {...}, "rag": {...}}
                for p_type, p_cfg in deployment.policies.items():
                    apply_policy(p_type, p_cfg)

        except Exception as e:
            # Log error?
            return {"valid": False, "error": f"Policy Logic Error: {str(e)}"}

        final_result = {
            "valid": True,
            "deployment": deployment_dict,
            "config": config,
            "user_id_context": f"apikey:{auth_key_record.id}",
            "log_payloads": bool(organization.log_payloads)
            if hasattr(organization, "log_payloads")
            else True,
        }

        # Update Cache
        self.context_cache[cache_key] = final_result
        return final_result

    async def check_policy(self, user: UserContext, action: str, resource: str) -> bool:
        """
        Check if a user is allowed to perform an action on a resource based on defined policies.
        Currently primarily delegates to RBAC for permissions and this engine for limits.
        """
        # Placeholder for complex policy logic
        return True

    async def check_quota(
        self, db: AsyncSession, user_id: str, model: str = "default"
    ) -> None:
        """
        Check if user has exceeded their quota for the day using Redis.
        Raises HTTPException if quota exceeded.
        """
        today_str = date.today().isoformat()

        # Redis Keys
        req_key = f"usage:{user_id}:{today_str}:{model}:requests"
        tok_key = f"usage:{user_id}:{today_str}:{model}:tokens"

        # Initialize limits with defaults
        limit_requests = DEFAULT_DAILY_REQUEST_LIMIT
        limit_tokens = DEFAULT_DAILY_TOKEN_LIMIT

        # Resolve Org ID for Policy Lookup (Optimized: only if needed to overwrite defaults)
        # Note: We still access DB here for Policy Config, which is cacheable or less frequent than Usage write.
        # Ideally Policy Config should also be part of the cached context passed in, but signature is fixed for now.

        # 1. Extract API Key ID from user_id and find Org ID
        org_id = None
        if user_id.startswith("apikey:"):
            try:
                api_key_id = user_id.split(":")[1]
                if api_key_id in self.org_id_cache:
                    org_id = self.org_id_cache[api_key_id]
                else:
                    stmt = select(DBApiKey.org_id).where(DBApiKey.id == api_key_id)
                    result = await db.execute(stmt)
                    org_id = result.scalars().first()
                    if org_id:
                        self.org_id_cache[api_key_id] = org_id
            except (ValueError, IndexError):
                pass

        # 2. If Org ID found, fetch quota policy
        if org_id:
            if org_id in self.quota_policy_cache:
                policy_config = self.quota_policy_cache[org_id]
                limit_requests = policy_config.get("request_limit", limit_requests)
                limit_tokens = policy_config.get("token_limit", limit_tokens)
            else:
                stmt = select(DBPolicy).where(
                    (DBPolicy.org_id == org_id)
                    & (DBPolicy.policy_type == "quota")
                    & (DBPolicy.deployment_id.is_(None))  # Org-wide quota policy
                )
                result = await db.execute(stmt)
                quota_policy = result.scalars().first()

                if quota_policy and quota_policy.config_json:
                    policy_config = quota_policy.config_json
                    self.quota_policy_cache[org_id] = policy_config
                    limit_requests = policy_config.get("request_limit", limit_requests)
                    limit_tokens = policy_config.get("token_limit", limit_tokens)

        # 3. Check Redis Usage with Circuit Breaker
        try:
            current_requests, current_tokens = await self._check_redis_quota(
                req_key, tok_key
            )

            used_requests = int(current_requests) if current_requests else 0
            used_tokens = int(current_tokens) if current_tokens else 0

            if used_requests >= limit_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Daily quota exceeded (Request Limit). Limit: {limit_requests}. Used: {used_requests}.",
                )
            if used_tokens >= limit_tokens:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Daily quota exceeded (Token Limit). Limit: {limit_tokens}. Used: {used_tokens}.",
                )

        except CircuitBreakerError:
            # Circuit breaker is open - Redis is down, fail open for availability
            logger.warning(
                "Redis circuit breaker is OPEN - allowing request (fail open)"
            )
            pass
        except redis.RedisError as e:
            # Other Redis errors - log and fail open
            logger.error(f"Redis Quota Check Failed - Failing Open: {e}")
            pass

    @circuit_breaker(
        "redis_quota",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=redis.RedisError,
    )
    async def _check_redis_quota(self, req_key: str, tok_key: str) -> tuple:
        """Check Redis quota with circuit breaker protection."""
        current_requests = await self.redis.get(req_key)
        current_tokens = await self.redis.get(tok_key)
        return current_requests, current_tokens

    async def increment_usage(
        self, db: AsyncSession, user_id: str, model: str, usage_data: Dict[str, int]
    ) -> None:
        """
        Increment user's quota usage in Redis (Primary) and Async Postgres (Secondary).
        """
        # 1. Redis Increment (Atomic & Immediate)
        await self.increment_redis_only(user_id, model, usage_data)

        # 2. Postgres Persistence (Ideally backgrounded, but keeping signature for now)
        await self.persist_usage_db(db, user_id, model, usage_data)

    async def increment_redis_only(
        self, user_id: str, model: str, usage_data: Dict[str, int]
    ) -> None:
        """Increment usage in Redis for real-time quota checks with circuit breaker."""
        today_str = date.today().isoformat()
        total_incr = usage_data.get("total_tokens", 0)

        req_key = f"usage:{user_id}:{today_str}:{model}:requests"
        tok_key = f"usage:{user_id}:{today_str}:{model}:tokens"

        try:
            await self._increment_redis_with_breaker(req_key, tok_key, total_incr)
        except CircuitBreakerError:
            logger.warning("Redis circuit breaker is OPEN - skipping usage increment")
        except redis.RedisError as e:
            logger.error(f"Redis Usage Increment Failed: {e}")

    @circuit_breaker(
        "redis_quota",
        failure_threshold=3,
        recovery_timeout=30.0,
        expected_exception=redis.RedisError,
    )
    async def _increment_redis_with_breaker(
        self, req_key: str, tok_key: str, total_incr: int
    ) -> None:
        """Increment Redis usage with circuit breaker protection."""
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.incr(req_key, amount=1)
            await pipe.incrby(tok_key, amount=total_incr)
            await pipe.expire(req_key, 172800)
            await pipe.expire(tok_key, 172800)
            await pipe.execute()

    async def persist_usage_db(
        self, db: AsyncSession, user_id: str, model: str, usage_data: Dict[str, int]
    ) -> None:
        """Persist usage to Postgres for long-term reporting."""
        today = date.today()
        prompt_incr = usage_data.get("prompt_tokens", 0)
        compl_incr = usage_data.get("completion_tokens", 0)
        total_incr = usage_data.get("total_tokens", 0)

        stmt = insert(DBUsage).values(
            user_id=user_id,
            date=today,
            model=model,
            request_count=1,
            prompt_tokens=prompt_incr,
            completion_tokens=compl_incr,
            total_tokens=total_incr,
        )

        stmt = stmt.on_conflict_do_update(
            constraint="_user_daily_model_usage_uc",
            set_={
                "request_count": DBUsage.request_count + 1,
                "prompt_tokens": DBUsage.prompt_tokens + prompt_incr,
                "completion_tokens": DBUsage.completion_tokens + compl_incr,
                "total_tokens": DBUsage.total_tokens + total_incr,
                "updated_at": func.now(),
            },
        )

        try:
            await db.execute(stmt)
            await db.commit()
        except Exception as e:
            logger.error(f"DB Usage Persist Failed: {e}")

    async def get_quotas(self, db: AsyncSession, user_id: str) -> dict:
        """
        Get usage quotas for a user for today (from Redis if available).
        """
        today_str = date.today().isoformat()

        req_key = (
            f"usage:{user_id}:{today_str}:*:requests"  # We need model breakdown or sum?
        )
        # Redis keys include model, so getting total requires pattern match or sum.
        # Simple implementation: Fallback to DB for "Get Quota" view since it aggregates.
        # Or just return default limits.

        # For simplicity in this phase, let's read from DB for the dashboard view to ensure consistency with historical data.
        # Redis is optimization for real-time checks. DB is source of truth for reporting.

        today = date.today()
        stmt = select(
            func.sum(DBUsage.total_tokens), func.sum(DBUsage.request_count)
        ).where((DBUsage.user_id == user_id) & (DBUsage.date == today))
        result = await db.execute(stmt)
        total_tokens, total_requests = result.first()

        return {
            "limit_requests": DEFAULT_DAILY_REQUEST_LIMIT,
            "used_requests": total_requests or 0,
            "limit_tokens": DEFAULT_DAILY_TOKEN_LIMIT,
            "used_tokens": total_tokens or 0,
            "reset_at": "23:59:59 (UTC)",
        }


policy_engine = PolicyEngine()
