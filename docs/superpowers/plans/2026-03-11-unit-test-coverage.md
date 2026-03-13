# Unit Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ~230 critical-path unit tests across all Python services, raising coverage from ~15% to ~40-50%.

**Architecture:** Three horizontal layers (security → error handling → complex logic) implemented across all services. Each layer produces a commit. Shared test infrastructure (conftest.py files) created first.

**Tech Stack:** pytest, pytest-asyncio, unittest.mock, httpx (AsyncClient/ASGITransport), fakeredis, bcrypt, cryptography, tiktoken

---

## File Structure

### New Files to Create
- `package/src/inferia/common/tests/conftest.py`
- `package/src/inferia/common/tests/test_middleware.py`
- `package/src/inferia/common/tests/test_rate_limit.py`
- `package/src/inferia/common/tests/test_errors.py`
- `package/src/inferia/common/tests/test_circuit_breaker.py`
- `package/src/inferia/common/tests/test_exception_handlers.py`
- `package/src/inferia/common/tests/test_http_client.py`
- `package/src/inferia/services/api_gateway/tests/test_auth_security.py`
- `package/src/inferia/services/api_gateway/tests/test_encryption.py`
- `package/src/inferia/services/api_gateway/tests/test_db_security.py`
- `package/src/inferia/services/inference/tests/conftest.py`
- `package/src/inferia/services/guardrail/tests/conftest.py`
- `package/src/inferia/services/guardrail/tests/test_scan_endpoint_security.py`
- `package/src/inferia/services/guardrail/tests/test_engine_error_handling.py`
- `package/src/inferia/services/guardrail/tests/test_engine_orchestration.py`
- `package/src/inferia/services/data/tests/conftest.py`
- `package/src/inferia/services/data/tests/test_upload_security.py`
- `package/src/inferia/services/data/tests/test_engine_error_handling.py`
- `package/src/inferia/services/data/tests/test_chunker.py`
- `package/src/inferia/services/data/tests/test_prompt_engine.py`
- `package/src/inferia/services/inference/tests/test_service_error_handling.py`

### Existing Files to Modify
- `package/src/inferia/services/api_gateway/tests/conftest.py` (extend)
- `.github/workflows/test.yml` (add new test files)

See spec at `docs/superpowers/specs/2026-03-11-unit-test-coverage-design.md` for full test descriptions per file.

---

## Implementation Sequence

Implementation follows the spec's 3-layer approach. Each layer is one commit + CI update.

### Phase 0: Shared test infrastructure (conftest.py files)
### Phase 1: Layer 1 — Security tests (~90 tests)
### Phase 2: Layer 2 — Error handling tests (~85 tests)
### Phase 3: Layer 3 — Complex logic tests (~55 tests)
### Phase 4: CI workflow update and verification
