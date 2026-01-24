"""
API Gateway router for inference endpoints.
Handles request routing to the orchestration layer.
"""

from typing import Any, Dict, List

from db.database import get_db
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from guardrail.api_models import GuardrailScanRequest, ScanType
from guardrail.engine import guardrail_engine
from guardrail.pii_service import pii_service
from models import InferenceRequest, InferenceResponse, ModelInfo, ModelsListResponse
from rbac import router as auth_router
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from db.models import Deployment

from gateway.rate_limiter import rate_limiter
from security.encryption import LogEncryption
from config import settings
 

import logging

encryption_service = None
try:
    if settings.log_encryption_key:
        encryption_service = LogEncryption(settings.log_encryption_key)
except Exception as e:
    # Fail fast if key is invalid
    print(f"Failed to initialize log encryption: {e}")


router = APIRouter(prefix="/internal", tags=["Internal Inference"])
router.include_router(auth_router.router)



logger = logging.getLogger(__name__)
logger.info("Loaded Internal Gateway Router")

# --- Policy Engine: Internal Endpoints ---
from policy.engine import policy_engine
from pydantic import BaseModel


class QuotaCheckRequest(BaseModel):
    user_id: str
    model: str = "default"


class UsageTrackRequest(BaseModel):
    user_id: str
    model: str
    usage: Dict[str, int]  # prompt_tokens, completion_tokens, total_tokens


@router.post("/policy/check_quota")
async def check_user_quota(
    request: QuotaCheckRequest, db: AsyncSession = Depends(get_db)
):
    """
    Check if user has sufficient quota.
    Raises 429 if exceeded.
    """
    await policy_engine.check_quota(db, request.user_id, request.model)
    return {"status": "ok", "message": "Quota within limits"}


@router.post("/policy/track_usage")
async def track_user_usage(
    request: UsageTrackRequest, db: AsyncSession = Depends(get_db)
):
    """
    Increment user usage stats.
    """
    await policy_engine.increment_usage(
        db, request.user_id, request.model, request.usage
    )
    return {"status": "ok", "message": "Usage tracked"}


# --- Inference Logging ---
import uuid

from db.models import InferenceLog
from models import InferenceLogCreate


@router.post("/logs/create")
async def create_inference_log(
    log_data: InferenceLogCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create an inference log entry.
    Internal endpoint called by Inference Gateway after each request.
    """
    log = InferenceLog(
        id=str(uuid.uuid4()),
        deployment_id=log_data.deployment_id,
        user_id=log_data.user_id,
        model=log_data.model,
        request_payload=(
            {"encrypted": True, "ciphertext": encryption_service.encrypt(log_data.request_payload)}
            if encryption_service and log_data.request_payload
            else log_data.request_payload
        ),
        latency_ms=log_data.latency_ms,
        ttft_ms=log_data.ttft_ms,
        tokens_per_second=log_data.tokens_per_second,
        prompt_tokens=log_data.prompt_tokens,
        completion_tokens=log_data.completion_tokens,
        total_tokens=log_data.total_tokens,
        status_code=log_data.status_code,
        error_message=log_data.error_message,
        is_streaming=log_data.is_streaming,
        applied_policies=log_data.applied_policies,
    )
    db.add(log)
    await db.commit()
    return {"status": "ok", "log_id": log.id}


@router.post("/guardrails/scan", response_model=dict)
async def scan_content(request_body: GuardrailScanRequest, request: Request):
    """
    Directly access the guardrail scanner for testing or external checks.
    """
    # Check rate limit
    await rate_limiter.check_rate_limit(request)

    # Get authenticated user (if available) or use context from body
    user_id = "unknown"

    # Use UserContext from middleware (Pydantic model, safe)
    if hasattr(request.state, "user"):
        user = request.state.user
        user_id = user.user_id
    elif hasattr(request_body, "user_id") and request_body.user_id:
        # Internal M2M call
        user_id = request_body.user_id

    # PII Pre-scan (Separates Service)
    # Check config for pii_enabled
    # Use config from request body
    config = request_body.config or {}
    pii_enabled = config.get("pii_enabled", False)

    # Legacy fallback: Check if "PII" is in input_scanners if pii_enabled not explicitly set
    if "pii_enabled" not in config:
        # If input_scanners is present, check for PII legacy keywords
        input_scanners = config.get("input_scanners", [])
        if "PII" in input_scanners or "Anonymize" in input_scanners:
            pii_enabled = True


    import asyncio
    
    # Run PII Scan (if enabled) and Guardrail Scan in parallel
    scan_coros = []
    
    # 1. Prepare PII Scan
    async def run_pii():
        if pii_enabled:
            return await pii_service.anonymize(request_body.text, request_body.pii_entities or [])
        return request_body.text, []

    scan_coros.append(run_pii())

    # 2. Prepare Guardrail Scan (Input or Output)
    async def run_guardrail(text_to_scan):
        # NOTE: Guardrails run on ORIGINAL text to detect malicious intent effectively.
        # Running on sanitized text might mask injection attacks that rely on PII structures.
        if request_body.scan_type == ScanType.INPUT:
             return await guardrail_engine.scan_input(
                prompt=text_to_scan,
                user_id=user_id,
                custom_keywords=request_body.custom_banned_keywords or [],
                pii_entities=request_body.pii_entities or [],
                config=request_body.config or {},
            )
        else:
             # Output scan requires context
             context = request_body.context or ""
             return await guardrail_engine.scan_output(
                prompt=context,
                output=text_to_scan,
                user_id=user_id,
                custom_keywords=request_body.custom_banned_keywords or [],
                config=request_body.config or {},
            )

    # We launch PII and Guardrail simultaneously. 
    # Guardrail sees the raw input (request_body.text).
    scan_coros.append(run_guardrail(request_body.text))


    # Execute
    results = await asyncio.gather(*scan_coros)
    
    # Unpack
    (sanitized_text, pii_violations) = results[0]
    result = results[1]

    # Merge PII Violations into Guardrail Result
    if pii_violations:
        result.violations.extend(pii_violations)
        # If PII was found, ensure result reflects sanitized text
        # Logic: If Guardrail didn't modify text (e.g. no toxicity blocking), use PII sanitized version.
        # If Guardrail BLOCKED it, sanitized text might be empty/masked differently.
        # We prioritize PII sanitization for the final output if the request is valid.

        if (
            result.is_valid
        ):  # Only override if valid so far? No, always enforce PII sanitization
            result.sanitized_text = sanitized_text  # guardrail might have sanitized further, but PII logic takes precedence?
            # Actually, guardrail engine might have sanitized OTHER things.
            # If guardrail returned sanitized_text different from input 'sanitized_text', lets keep it.
            # But if guardrail just returned 'sanitized_text' (input), then we are good.
            # Logic: If guardrail result.sanitized_text IS the input we gave it, and we changed it from original, update it?
            # Simple approach: If PII changed text, mark as anonymized.
            pass

    # Ensure final sanitized text reflects PII changes if engine didn't change it further
    if sanitized_text != request_body.text:
        if result.sanitized_text == sanitized_text:
            # Engine didn't change it further
            pass
        elif result.sanitized_text == request_body.text:
            # Engine ignored our sanitized input? Unlikely.
            result.sanitized_text = sanitized_text

        if "anonymized" not in result.actions_taken:
            result.actions_taken.append("anonymized")

    return {
        "is_valid": result.is_valid,
        "sanitized_text": result.sanitized_text,
        "risk_score": result.risk_score,
        "violations": [
            {
                "scanner": v.scanner,
                "type": v.violation_type,
                "score": v.score,
                "details": v.details,
            }
            for v in result.violations
        ],
        "scan_time_ms": result.scan_time_ms,
        "actions_taken": result.actions_taken,
    }


@router.get("/models", response_model=ModelsListResponse)
async def list_models(request: Request, db: AsyncSession = Depends(get_db)):
    """
    List available models.
    """
    # Check rate limit
    await rate_limiter.check_rate_limit(request)

    # Get available models from database (real deployments)
    result = await db.execute(select(Deployment))
    deployments = result.scalars().all()
    
    mock_models = [
        ModelInfo(
            id=str(d.model_name),
            created=int(d.created_at.timestamp()) if d.created_at is not None else 0,
            owned_by=str(d.org_id) if d.org_id is not None else "system",
            description=f"Model deployment for {d.model_name} ({d.engine})"
        )
        for d in deployments
    ]
    
    return ModelsListResponse(data=mock_models)


# NEW: Context Resolution for Inference Gateway
from db.database import get_db
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select


class ResolveContextRequest(BaseModel):
    api_key: str
    model: str


from typing import Any, Dict, Optional


class ResolveContextResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    deployment: Optional[Dict] = None
    guardrail_config: Optional[Dict] = None
    rag_config: Optional[Dict] = None
    template_config: Optional[Dict] = None
    rate_limit_config: Optional[Dict] = None
    user_id_context: Optional[str] = None
    org_id: Optional[str] = None
    log_payloads: bool = True


@router.post("/context/resolve", response_model=ResolveContextResponse)
async def resolve_inference_context(
    request: ResolveContextRequest, db: AsyncSession = Depends(get_db)
):
    """
    Resolve inference context from API Key and Model.
    Used by Inference Gateway to fetch config.
    """
    # Delegate to Policy Engine
    result = await policy_engine.resolve_context(db, request.api_key, request.model)

    if not result["valid"]:
        return ResolveContextResponse(valid=False, error=result["error"])

    deployment = result["deployment"]
    config = result["config"]
    user_id_context = result["user_id_context"]

    return ResolveContextResponse(
        valid=True,
        deployment={
            "id": deployment["id"],
            "model_name": deployment["model_name"],
            "endpoint": deployment["endpoint"],
            "engine": deployment["engine"],
            "configuration": deployment["configuration"],
            "inference_model": deployment.get("inference_model"),
        },
        guardrail_config=config["guardrail"],
        rag_config=config["rag"],
        template_config=config.get("prompt_template"),
        rate_limit_config=config.get("rate_limit"),
        user_id_context=user_id_context,
        org_id=deployment["org_id"],
        log_payloads=result.get("log_payloads", True),
    )


# --- Prompt Engine Integration ---
from models import Message, PromptProcessRequest, PromptProcessResponse
from prompt.engine import prompt_engine


@router.post("/prompt/process", response_model=PromptProcessResponse)
async def process_prompt(
    request: PromptProcessRequest,
    # Internal endpoint, simpler auth or rely on firewall/internal network
    db: AsyncSession = Depends(get_db),
):
    """
    Process a prompt: Rewrite -> RAG -> Template.
    Returns the modified messages list.
    """
    messages = request.messages
    org_id = request.org_id
    if not messages:
        return PromptProcessResponse(messages=[])

    # 1. Identify User Query (Last User Message)
    user_msg_idx = -1
    for i, m in enumerate(reversed(messages)):
        if m.role == "user":
            user_msg_idx = len(messages) - 1 - i
            break

    if user_msg_idx == -1:
        # No user message, return as is
        return PromptProcessResponse(messages=messages)

    original_query = messages[user_msg_idx].content
    processed_query = original_query

    # 2. Rewrite (Disabled)
    rewritten = False
    # Rewriting functionality removed as per request.

    # 4. Advanced Templating with Variable Mapping
    used_template_id = request.template_id
    template_content = request.template_content

    # If config provides a base template, prefer that, unless override is present
    template_config = request.template_config or {}
    rag_used = False  # Initialize for scope

    # Only use template config if explicitly enabled
    template_enabled = template_config.get("enabled", False)

    if not used_template_id and template_enabled:
        used_template_id = template_config.get("base_template_id")
        # If content override is present in config, use it
        if template_config.get("content"):
            template_content = template_config.get("content")
            used_template_id = used_template_id or "custom_override"

    # If we have a template to work with AND templates are enabled
    if (used_template_id or template_content) and template_enabled:
        variables = request.template_vars or {}
        # Only use variable_mapping if template is enabled via config
        variable_mapping = (
            template_config.get("variable_mapping", {}) if template_enabled else {}
        )

        # Resolve mapped variables
        for var_name, config in variable_mapping.items():
            source = config.get("source")
            if source == "rag":
                collection = config.get("collection_id") or "default"
                top_k = config.get("top_k", 3)
                # Fetch RAG context for this variable
                rag_val = await prompt_engine.assemble_context(
                    processed_query, collection, org_id or "default", top_k
                )
                if rag_val:
                    variables[var_name] = rag_val
                    rag_used = True
            elif source == "static":
                variables[var_name] = config.get("value", "")
            elif source == "request":
                # Key alias, e.g. map "user_name" var to "user" payload key
                key = config.get("key", var_name)
                # If key exists in request vars, use it. Otherwise keep existing or ignore.
                if key in variables:
                    variables[var_name] = variables[key]

        # Always inject standard vars if not mapped (backward compat)
        if "query" not in variables:
            variables["query"] = processed_query

        # Legacy RAG support (if rag_config passed but no mapping used)
        if request.rag_config and request.rag_config.get("enabled") and not rag_used:
            # If RAG is enabled but not mapped to a variable, we put it in 'context'
            # OR fallback to appending to user msg if 'context' var not in template?
            # Let's put in 'context' variable for compatibility.
            if "context" not in variables:
                collection = request.rag_config.get("default_collection") or "default"
                top_k = request.rag_config.get("top_k", 3)
                rag_ctx = await prompt_engine.assemble_context(
                    processed_query, collection, org_id or "default", top_k
                )
                if rag_ctx:
                    variables["context"] = rag_ctx
                    rag_used = True

        # Render
        if template_content:
            system_content = prompt_engine.process_prompt_from_content(
                template_content, variables
            )
            used_template_id = used_template_id or "dynamic"
        else:
            # We need to fetch the template content if we only have ID
            # Ideally prompt_engine.process_prompt does this via DB lookup.
            # Assuming prompt_engine handles it.
            system_content = prompt_engine.process_prompt(used_template_id, variables)

        # Replace or Insert System Message
        messages = [m for m in messages if m.role != "system"]
        messages.insert(0, Message(role="system", content=system_content))

    # Fallback RAG Logic (No Template, but RAG Enabled)
    elif not rag_used and request.rag_config and request.rag_config.get("enabled"):
        collection = request.rag_config.get("default_collection") or "default"
        top_k = request.rag_config.get("top_k", 3)
        rag_context = await prompt_engine.assemble_context(
            processed_query, collection, org_id or "default", top_k
        )

        if rag_context:
            rag_used = True
            # Strategy: Append to User Message (easiest for non-template flows)
            context_msg = f"Context Information:\n{rag_context}\n\n"
            messages[user_msg_idx].content = context_msg + processed_query

    return PromptProcessResponse(
        messages=messages,
        used_template_id=used_template_id,
        rewritten=rewritten,
        rag_context_used=rag_used,
    )
