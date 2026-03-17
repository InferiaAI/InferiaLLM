"""
Verify that all Kubernetes API calls in async methods are wrapped
with _run_sync (run_in_executor) so they never block the event loop.
"""

import ast
import inspect
import textwrap
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_source(module):
    """Import a module by name and return its dedented source."""
    mod = __import__(module, fromlist=["_"])
    return textwrap.dedent(inspect.getsource(mod))


def _async_methods_with_blocking_calls(source: str, blocked_prefixes: list[str]):
    """
    Parse *source* and return a list of (method_name, [offending_call, ...])
    for every ``async def`` that contains a direct call to any of the
    *blocked_prefixes* (e.g. ``self.core``, ``self.api``) **outside** of a
    ``_run_sync(...)`` wrapper.
    """
    tree = ast.parse(source)
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef,)):
            continue

        bad_calls: list[str] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue

            # Check if this call is _run_sync(...) — skip its children
            func = child.func
            if isinstance(func, ast.Name) and func.id == "_run_sync":
                continue
            if isinstance(func, ast.Attribute) and func.attr == "_run_sync":
                continue

            # Build the dotted name of the callee
            callee = _dotted(child.func)
            if callee and any(callee.startswith(p) for p in blocked_prefixes):
                # Make sure it is NOT the first arg of an enclosing _run_sync
                if not _is_arg_of_run_sync(child, source):
                    bad_calls.append(callee)

        if bad_calls:
            violations.append((node.name, bad_calls))

    return violations


def _dotted(node) -> str | None:
    """Reconstruct a dotted name from an AST node (``a.b.c``)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_arg_of_run_sync(call_node: ast.Call, source: str) -> bool:
    """
    Return True if *call_node*'s function reference appears as the first
    positional argument of a ``_run_sync(...)`` call.  We do this by
    re-walking from the module root and checking enclosing Call nodes.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_run_sync = (isinstance(func, ast.Name) and func.id == "_run_sync") or (
            isinstance(func, ast.Attribute) and func.attr == "_run_sync"
        )
        if not is_run_sync:
            continue
        # The blocked method reference is passed as the first positional arg
        # (not called directly), so there should be no Call node wrapping it
        # inside _run_sync — the Call is _run_sync itself.
        # We just need to verify our detected call_node IS _run_sync's Call.
        # Since AST nodes are unique objects we cannot match by identity after
        # a re-parse, so we match by line + col.
        if node.lineno == call_node.lineno and node.col_offset == call_node.col_offset:
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


MODULES_AND_PREFIXES = [
    (
        "inferia.services.orchestration.services.adapter_engine.adapters.k8s.k8s_adapter",
        ["self.core"],
    ),
    (
        "inferia.services.orchestration.services.llmd_runtime.client",
        ["self.api"],
    ),
    (
        "inferia.services.orchestration.infra.k8s_llmd_client",
        ["self.api"],
    ),
]


@pytest.mark.parametrize("module,prefixes", MODULES_AND_PREFIXES)
def test_no_blocking_k8s_calls_in_async_methods(module, prefixes):
    """Every self.core.* / self.api.* call in an async def must go through _run_sync."""
    source = _get_source(module)
    violations = _async_methods_with_blocking_calls(source, prefixes)
    if violations:
        report = "\n".join(
            f"  {method}: {', '.join(calls)}" for method, calls in violations
        )
        pytest.fail(
            f"Blocking K8s calls found in async methods of {module}:\n{report}"
        )
