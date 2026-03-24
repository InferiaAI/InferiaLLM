"""Tests for orchestrator guardrail lifecycle fixes (#43, #51, #65).

Issue #43: scan_task should not be created when guardrails are disabled.
Issue #51: Output guardrail must handle null content (tool-call responses).
Issue #65: asyncio.create_task in streaming finally must be wrapped in try/except.
"""

import ast
import asyncio
import inspect
import textwrap

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Base patch path — completion handler module
_HANDLER = "inferia.services.inference.core.handlers.completion"


def _make_context(guardrail_enabled=False, pii_enabled=False):
    """Return a minimal resolve_context result."""
    return {
        "deployment": {
            "id": "dep-1",
            "endpoint": "http://provider:8000",
            "engine": "vllm",
        },
        "user_id_context": "user-1",
        "org_id": "org-1",
        "guardrail_config": {
            "enabled": guardrail_enabled,
            "pii_enabled": pii_enabled,
        },
        "rag_config": {"enabled": False},
        "template_config": None,
        "rate_limit_config": None,
        "log_payloads": True,
    }


def _patch_completion_deps():
    """Context manager that patches all completion handler dependencies."""
    return (
        patch(f"{_HANDLER}.GatewayService"),
        patch(f"{_HANDLER}.api_gateway_client"),
        patch(f"{_HANDLER}.rate_limiter"),
        patch(f"{_HANDLER}.get_adapter"),
        patch(f"{_HANDLER}.settings"),
    )


# ---------------------------------------------------------------------------
# Issue #43 — scan_task must NOT be created when guardrails are disabled
# ---------------------------------------------------------------------------

class TestScanTaskConditional:
    """Verify scan_task is only created when guardrails are actually enabled."""

    @pytest.mark.asyncio
    async def test_no_scan_when_guardrails_disabled(self):
        """When guardrail_cfg has enabled=False and pii_enabled absent,
        GatewayService.scan_input must NOT be called."""
        from inferia.services.inference.core.orchestrator import OrchestrationService

        context = _make_context(guardrail_enabled=False, pii_enabled=False)

        with patch(
            f"{_HANDLER}.GatewayService"
        ) as mock_gw, patch(
            f"{_HANDLER}.api_gateway_client"
        ) as mock_client, patch(
            f"{_HANDLER}.rate_limiter"
        ), patch(
            f"{_HANDLER}.get_adapter"
        ) as mock_adapter, patch(
            f"{_HANDLER}.settings"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=context)
            mock_gw.scan_input = AsyncMock(return_value=None)
            mock_gw.process_prompt = AsyncMock(
                return_value=[{"role": "user", "content": "hi"}]
            )
            mock_gw.call_upstream = AsyncMock(
                return_value={
                    "choices": [
                        {"message": {"content": "hello"}}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            )
            mock_gw.scan_output = AsyncMock(return_value=None)
            mock_client.check_quota = AsyncMock(return_value=None)
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            adapter = MagicMock()
            adapter.get_headers.return_value = {}
            adapter.transform_request.side_effect = lambda x: x
            mock_adapter.return_value = adapter

            bg = BackgroundTasks()
            await OrchestrationService.handle_completion(
                api_key="test-key",
                body={"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
                background_tasks=bg,
            )

            mock_gw.scan_input.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_called_when_guardrails_enabled(self):
        """When guardrails are enabled, scan_input IS called."""
        from inferia.services.inference.core.orchestrator import OrchestrationService

        context = _make_context(guardrail_enabled=True, pii_enabled=False)

        with patch(
            f"{_HANDLER}.GatewayService"
        ) as mock_gw, patch(
            f"{_HANDLER}.api_gateway_client"
        ) as mock_client, patch(
            f"{_HANDLER}.rate_limiter"
        ), patch(
            f"{_HANDLER}.get_adapter"
        ) as mock_adapter, patch(
            f"{_HANDLER}.settings"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=context)
            mock_gw.scan_input = AsyncMock(return_value=None)
            mock_gw.process_prompt = AsyncMock(
                return_value=[{"role": "user", "content": "hi"}]
            )
            mock_gw.call_upstream = AsyncMock(
                return_value={
                    "choices": [
                        {"message": {"content": "hello"}}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            )
            mock_gw.scan_output = AsyncMock(return_value=None)
            mock_client.check_quota = AsyncMock(return_value=None)
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            adapter = MagicMock()
            adapter.get_headers.return_value = {}
            adapter.transform_request.side_effect = lambda x: x
            mock_adapter.return_value = adapter

            bg = BackgroundTasks()
            await OrchestrationService.handle_completion(
                api_key="test-key",
                body={"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
                background_tasks=bg,
            )

            mock_gw.scan_input.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #51 — Output guardrail must handle null content gracefully
# ---------------------------------------------------------------------------

class TestOutputGuardrailNullContent:
    """Verify output guardrail doesn't crash on tool-call / null content."""

    @pytest.mark.asyncio
    async def test_tool_call_response_null_content(self):
        """When choices[0].message.content is None (tool call), no KeyError."""
        from inferia.services.inference.core.orchestrator import OrchestrationService

        context = _make_context(guardrail_enabled=True)

        # Tool-call response: content is None
        tool_call_response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [{"id": "call_1", "function": {"name": "f"}}],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with patch(
            f"{_HANDLER}.GatewayService"
        ) as mock_gw, patch(
            f"{_HANDLER}.api_gateway_client"
        ) as mock_client, patch(
            f"{_HANDLER}.rate_limiter"
        ), patch(
            f"{_HANDLER}.get_adapter"
        ) as mock_adapter, patch(
            f"{_HANDLER}.settings"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=context)
            mock_gw.scan_input = AsyncMock(return_value=None)
            mock_gw.process_prompt = AsyncMock(
                return_value=[{"role": "user", "content": "hi"}]
            )
            mock_gw.call_upstream = AsyncMock(return_value=tool_call_response)
            mock_gw.scan_output = AsyncMock(return_value=None)
            mock_client.check_quota = AsyncMock(return_value=None)
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            adapter = MagicMock()
            adapter.get_headers.return_value = {}
            adapter.transform_request.side_effect = lambda x: x
            mock_adapter.return_value = adapter

            bg = BackgroundTasks()
            result = await OrchestrationService.handle_completion(
                api_key="test-key",
                body={"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
                background_tasks=bg,
            )

            # scan_output should NOT be called because content is empty/None
            mock_gw.scan_output.assert_not_called()
            assert result == tool_call_response

    @pytest.mark.asyncio
    async def test_empty_choices_no_crash(self):
        """When choices list is empty, output guardrail doesn't crash."""
        from inferia.services.inference.core.orchestrator import OrchestrationService

        context = _make_context(guardrail_enabled=True)

        empty_response = {
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 0},
        }

        with patch(
            f"{_HANDLER}.GatewayService"
        ) as mock_gw, patch(
            f"{_HANDLER}.api_gateway_client"
        ) as mock_client, patch(
            f"{_HANDLER}.rate_limiter"
        ), patch(
            f"{_HANDLER}.get_adapter"
        ) as mock_adapter, patch(
            f"{_HANDLER}.settings"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=context)
            mock_gw.scan_input = AsyncMock(return_value=None)
            mock_gw.process_prompt = AsyncMock(
                return_value=[{"role": "user", "content": "hi"}]
            )
            mock_gw.call_upstream = AsyncMock(return_value=empty_response)
            mock_gw.scan_output = AsyncMock(return_value=None)
            mock_client.check_quota = AsyncMock(return_value=None)
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            adapter = MagicMock()
            adapter.get_headers.return_value = {}
            adapter.transform_request.side_effect = lambda x: x
            mock_adapter.return_value = adapter

            bg = BackgroundTasks()
            result = await OrchestrationService.handle_completion(
                api_key="test-key",
                body={"model": "m1", "messages": [{"role": "user", "content": "hi"}]},
                background_tasks=bg,
            )

            mock_gw.scan_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_multimodal_last_message_no_crash(self):
        """When last message content is a list (multimodal), no crash."""
        from inferia.services.inference.core.orchestrator import OrchestrationService

        context = _make_context(guardrail_enabled=True)

        response = {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        multimodal_messages = [
            {"role": "user", "content": [{"type": "image_url", "image_url": "..."}]}
        ]

        with patch(
            f"{_HANDLER}.GatewayService"
        ) as mock_gw, patch(
            f"{_HANDLER}.api_gateway_client"
        ) as mock_client, patch(
            f"{_HANDLER}.rate_limiter"
        ), patch(
            f"{_HANDLER}.get_adapter"
        ) as mock_adapter, patch(
            f"{_HANDLER}.settings"
        ):
            mock_gw.resolve_context = AsyncMock(return_value=context)
            mock_gw.scan_input = AsyncMock(return_value=None)
            mock_gw.process_prompt = AsyncMock(return_value=multimodal_messages)
            mock_gw.call_upstream = AsyncMock(return_value=response)
            mock_gw.scan_output = AsyncMock(return_value=None)
            mock_client.check_quota = AsyncMock(return_value=None)
            mock_client.log_inference = AsyncMock()
            mock_client.track_usage = AsyncMock()

            adapter = MagicMock()
            adapter.get_headers.return_value = {}
            adapter.transform_request.side_effect = lambda x: x
            mock_adapter.return_value = adapter

            bg = BackgroundTasks()
            # Build a body whose messages will be replaced by process_prompt
            # This must not raise KeyError or AttributeError
            result = await OrchestrationService.handle_completion(
                api_key="test-key",
                body={"model": "m1", "messages": multimodal_messages},
                background_tasks=bg,
            )

            # The key assertion: no crash occurred (no KeyError on multimodal content)
            assert result == response


# ---------------------------------------------------------------------------
# Issue #65 — asyncio.create_task in streaming finally must be guarded
# ---------------------------------------------------------------------------

class TestStreamingFinallyGuarded:
    """Verify that asyncio.create_task in streaming generator finally blocks
    is wrapped in try/except RuntimeError."""

    def test_no_bare_create_task_in_finally(self):
        """Source code must not have a bare asyncio.create_task inside a
        finally block without a try/except guard."""
        # After refactor, the streaming logic lives in handlers/completion.py
        import inferia.services.inference.core.handlers.completion as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.finalbody:
                # Walk every node in the finally body
                for child in ast.walk(handler):
                    if not (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "create_task"
                    ):
                        continue
                    # Found asyncio.create_task in a finally block.
                    # It must be inside a nested Try with an except handler.
                    # Walk upward from the finally to check for wrapping try.
                    _assert_create_task_is_guarded(node.finalbody)
                    return  # Only one occurrence expected

        # If we get here, no asyncio.create_task in finally at all — that's OK
        # (means it was refactored away entirely).


def _assert_create_task_is_guarded(finally_body):
    """Assert that asyncio.create_task within finally_body is inside a try/except."""
    for node in finally_body:
        if isinstance(node, ast.Try) and node.handlers:
            # The create_task is inside a try/except — good
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "create_task"
                ):
                    return
    raise AssertionError(
        "asyncio.create_task found in finally block without try/except guard"
    )
