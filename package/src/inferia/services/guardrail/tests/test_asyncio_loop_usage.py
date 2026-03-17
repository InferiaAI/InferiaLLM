"""
Verify that guardrail modules use asyncio.get_running_loop() instead of the
deprecated asyncio.get_event_loop(), which raises RuntimeError on Python 3.12+
when no running loop exists.

Closes #49
"""

import ast
import pathlib

# Resolve paths relative to this test file so the correct worktree source is
# read regardless of which copy of the package is installed.
_GUARDRAIL_DIR = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contains_get_event_loop(source: str) -> list[int]:
    """Return line numbers where ``asyncio.get_event_loop()`` is called."""
    hits: list[int] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match: asyncio.get_event_loop()
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get_event_loop"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            hits.append(node.lineno)
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoDeprecatedGetEventLoop:
    """Ensure no source file uses asyncio.get_event_loop()."""

    def test_pii_service_uses_get_running_loop(self):
        source = (_GUARDRAIL_DIR / "pii_service.py").read_text()
        lines = _contains_get_event_loop(source)
        assert lines == [], (
            f"pii_service.py still calls asyncio.get_event_loop() on line(s) {lines}"
        )

    def test_llama_guard_provider_uses_get_running_loop(self):
        source = (_GUARDRAIL_DIR / "providers" / "llama_guard_provider.py").read_text()
        lines = _contains_get_event_loop(source)
        assert lines == [], (
            f"llama_guard_provider.py still calls asyncio.get_event_loop() on line(s) {lines}"
        )
