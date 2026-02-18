from fastapi import HTTPException
from inferia.services.inference.client import api_gateway_client
from typing import Dict, Any, List, AsyncGenerator
import logging
import httpx
from .http_client import http_client
from .providers import get_adapter

logger = logging.getLogger(__name__)


class GatewayService:
    @staticmethod
    async def resolve_context(api_key: str, model: str) -> Dict[str, Any]:
        """Resolves deployment context via Filtration Gateway."""
        context = await api_gateway_client.resolve_context(api_key, model)

        if not context.get("valid"):
            raise HTTPException(
                status_code=401, detail=context.get("error", "Unauthorized")
            )

        return context

    @staticmethod
    async def process_prompt(
        messages: List[Dict],
        model: str,
        user_context_id: str,
        org_id: str,
        rag_cfg: Dict,
        template_config: Dict,
        request_body: Dict,
    ) -> List[Dict]:
        """Handles Prompt Engineering (Rewriting, RAG, Templating)."""

        # Skip prompt processing if nothing is enabled
        rag_enabled = rag_cfg and rag_cfg.get("enabled", False)
        template_enabled = template_config and template_config.get("enabled", False)

        if not rag_enabled and not template_enabled:
            logger.debug("Skipping prompt processing - RAG and template both disabled")
            return messages

        # Extract extra template vars from body
        standard_fields = {
            "messages",
            "model",
            "stream",
            "temperature",
            "top_p",
            "n",
            "stop",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
            "logprobs",
            "top_logprobs",
            "seed",
            "tools",
            "tool_choice",
            "response_format",
        }
        template_vars = {
            k: v for k, v in request_body.items() if k not in standard_fields
        }

        process_payload = {
            "messages": messages,
            "model": model,
            "user_id": user_context_id,
            "org_id": org_id,
            "rag_config": rag_cfg,
            "template_id": template_config.get("id") if template_config else None,
            "template_content": template_config.get("content")
            if template_config
            else None,
            "template_config": template_config,
            "template_vars": template_vars,
        }

        try:
            processed_resp = await api_gateway_client.process_prompt(process_payload)
            if processed_resp.get("messages"):
                return processed_resp["messages"]
        except Exception as e:
            logger.error(f"Prompt processing error: {e}")
            # Fail closed to prevent security/policy bypass
            raise HTTPException(status_code=500, detail="Prompt processing failed")

    @staticmethod
    async def scan_input(messages: List[Dict], guardrail_cfg: Dict, user_id: str):
        """Scans input messages for guardrail violations."""
        if not guardrail_cfg.get("enabled") and not guardrail_cfg.get("pii_enabled"):
            return

        # Optimization: Only scan the *last* user message to save bandwidth/latency.
        # Scanning the entire history on every turn is redundant and expensive.
        last_message = messages[-1] if messages else {}
        input_text = (
            last_message.get("content", "")
            if last_message.get("role") == "user"
            else ""
        )

        if not input_text:
            return
        scan_payload = {
            "text": input_text,
            "scan_type": "input",
            "user_id": user_id,
            "pii_entities": guardrail_cfg.get("pii_entities"),
            "config": guardrail_cfg,  # Pass entire Guardrail Config
        }

        scan_result = await api_gateway_client.scan_content(None, scan_payload)

        if not scan_result.get("is_valid", True):
            violations = scan_result.get("violations", [])
            if not violations:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "guardrail_violation",
                        "message": "Input violated guardrails",
                    },
                )

            # 1. Detect if it's a scanner failure vs a safety violation
            v = violations[0]
            v_type = v.get("type", "")
            scanner_name = v.get("scanner", "Unknown")

            if v_type == "external_service_error":
                error_msg = f"Guardrail engine '{scanner_name}' failed: Check API Key or Configuration"
                error_code = "guardrail_error"
            else:
                # Friendly name mapping for common scanners
                friendly_shorthand = {
                    "Toxicity": "Toxicity",
                    "PromptInjection": "Prompt Injection",
                    "Secrets": "Sensitive Secrets",
                    "Code": "Malicious Code",
                    "LlamaGuard": "Llama Guard Safety",
                    "Lakera": "Lakera Guard Safety",
                }
                display_name = friendly_shorthand.get(scanner_name, scanner_name)
                error_msg = f"Inference blocked: {display_name} violation detected"
                error_code = "guardrail_violation"

            raise HTTPException(
                status_code=400,
                detail={"error": error_code, "message": error_msg, "violation": v},
            )

        # If we reach here, no blocking violations found.
        # Check PII (Anonymize) - if present, it's not an error, but we use sanitized text.
        # The engine logic automatically sets sanitized_text if PII is redacted.
        # We just need to ensure we apply it.

        # Apply Sanitization
        if scan_result.get("sanitized_text"):
            for m in reversed(messages):
                if m["role"] == "user":
                    m["content"] = scan_result["sanitized_text"]
                    break

    @staticmethod
    async def scan_output(
        output_text: str, context: str, guardrail_cfg: Dict, user_id: str
    ):
        """Scans output text for guardrail violations."""
        if not guardrail_cfg.get("enabled"):
            return

        # Optimization: Don't call if no output scanners enabled?
        # But now backend filters dynamically. We can just send it.
        if not guardrail_cfg.get("output_scanners"):
            return

        scan_payload = {
            "text": output_text,
            "scan_type": "output",
            "context": context,
            "user_id": user_id,
            "config": guardrail_cfg,  # Pass Config
        }

        scan_result = await api_gateway_client.scan_content(None, scan_payload)

        if not scan_result.get("is_valid", True):
            # Violations present
            violations = scan_result.get("violations", [])
            raise HTTPException(
                status_code=400, detail="Output content violated guardrails"
            )

    @staticmethod
    def _build_full_url(endpoint_url: str, chat_path: str) -> str:
        """
        Build the full URL, handling cases where endpoint already contains part of the path.
        Prevents duplicate paths like /v1/v1/chat/completions
        """
        # Strip trailing slashes from endpoint
        endpoint = endpoint_url.rstrip("/")

        # If endpoint already contains the full chat path, use it as-is
        if endpoint.endswith("/chat/completions") or endpoint.endswith("/messages"):
            return endpoint

        # If endpoint already contains /v1, don't add it again
        if endpoint.endswith("/v1"):
            # chat_path is like /v1/chat/completions, so we only need /chat/completions
            if chat_path.startswith("/v1"):
                return endpoint + chat_path[3:]  # Skip the /v1 part
            return endpoint + chat_path

        # Standard case - just append the path
        return endpoint + chat_path

    @staticmethod
    async def stream_upstream(
        endpoint_url: str, payload: Dict, headers: Dict, engine: str = "vllm"
    ) -> AsyncGenerator[bytes, None]:
        adapter = get_adapter(engine)
        chat_path = adapter.get_chat_path()
        transformed_payload = adapter.transform_request(payload)
        full_url = GatewayService._build_full_url(endpoint_url, chat_path)

        client = http_client.get_client()
        try:
            async with client.stream(
                "POST", full_url, json=transformed_payload, headers=headers
            ) as response:
                response.raise_for_status()
                # Use aiter_raw() for unbuffered streaming (important for SSE)
                async for chunk in response.aiter_raw():
                    yield chunk
        except httpx.HTTPStatusError as e:
            logger.error(f"Upstream Error {e.response.status_code}")
            yield f'data: {{"error": "Upstream Error: {e.response.status_code}"}}\n\n'.encode()
        except Exception as e:
            logger.error(f"Streaming Exception: {e}")
            yield f'data: {{"error": "Streaming Failed: {str(e)}"}}\n\n'.encode()

    @staticmethod
    async def call_upstream(
        endpoint_url: str, payload: Dict, headers: Dict, engine: str = "vllm"
    ) -> Dict:
        adapter = get_adapter(engine)
        chat_path = adapter.get_chat_path()
        transformed_payload = adapter.transform_request(payload)
        full_url = GatewayService._build_full_url(endpoint_url, chat_path)

        client = http_client.get_client()
        try:
            resp = await client.post(
                full_url, json=transformed_payload, headers=headers
            )
            resp.raise_for_status()
            raw_response = resp.json()

            # Transform response back to OpenAI format
            return adapter.transform_response(raw_response)
        except httpx.HTTPStatusError as e:
            logger.error(f"Provider error {e.response.status_code}: {e.response.text}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Provider error: {e.response.text}",
            )
        except Exception as e:
            logger.error(f"Provider request failed: {e}")
            raise HTTPException(
                status_code=502, detail=f"Upstream provider error: {str(e)}"
            )
