# Unit Test Coverage Plan: Critical Paths

**Date:** 2026-03-11
**Scope:** Critical paths only (~40-50% target coverage, up from ~15%)
**Approach:** Horizontal layers — security first, then error handling, then complex logic
**Dependencies:** Mocked with lightweight fakes (in-memory SQLite, fakeredis, mock external APIs)
**Test organization:** Follow each service's existing directory conventions

---

## Current State

| Service | Source LOC | Existing Tests | Est. Coverage |
|---------|-----------|----------------|---------------|
| API Gateway | 8,521 | 47 functions | ~15-20% |
| Orchestration | 10,114 | 37 functions | ~35% (mostly E2E) |
| Inference | ~2,000 | ~8 functions | ~3-5% |
| Guardrail | 1,479 | 7 functions | ~13-15% |
| Data | 1,537 | 10 functions | ~5% |
| Common/CLI | 3,346 | 23 functions | ~11% |
| **Total** | **~27,000** | **~132 functions** | **~15% avg** |

---

## Shared Test Infrastructure (Create First)

Before writing any layer tests, set up shared fixtures:

### `common/tests/conftest.py` (new)
- `fakeredis_client` — in-memory Redis for rate limiter tests
- `mock_http_client` — patched `InternalHttpClient` returning controlled responses

### `api_gateway/tests/conftest.py` (extend existing)
- `mock_encryption_key` — deterministic AES key for encryption roundtrip tests
- `mock_policy_engine` — pre-loaded quota/policy state

### `orchestration/test/conftest.py` (new)
- `mock_db_session` — AsyncMock SQLAlchemy session
- `fake_adapter` — in-memory adapter returning controlled provision/deprovision results
- `mock_grpc_context` — fake gRPC `ServicerContext` with abort tracking

### `inference/tests/conftest.py` (new)
- `mock_http_client` — patched httpx.AsyncClient for upstream calls
- `mock_concurrency_limiter` — controlled semaphore for request limiting
- `mock_provider_adapter` — returns controlled provider responses

### `data/tests/conftest.py` (new)
- `mock_chroma_client` — in-memory collection store
- `mock_file_upload` — `UploadFile` fixtures with controlled content/size/name

### `guardrail/tests/conftest.py` (new)
- `mock_llama_guard` — returns controlled scan results
- `mock_llm_guard` — returns controlled scanner outputs
- `mock_lakera` — returns controlled API responses
- `mock_config_manager` — static config, no polling

---

## Layer 1: Security Tests (~90 tests, ~2,000 LOC)

Focus: Auth, encryption, input validation, injection prevention.

### Common Module — 25 tests

**`common/tests/test_middleware.py`** (10 tests)
- Missing API key header -> 401
- Invalid API key -> 403
- Valid API key -> request passes through
- Unconfigured API key (None/empty) -> 503 fail-closed
- Skip paths bypass auth (`/health`, `/`)
- Path prefix matching works correctly
- Request ID generated as UUID and set in response header
- Request ID context variable set and reset (even on exception)
- Custom header name (`X-Internal-API-Key`) recognized
- Concurrent requests get independent request IDs

**`common/tests/test_rate_limit.py`** (10 tests)
- First request within window -> allowed
- Requests at exactly max_requests -> last one allowed
- Request exceeding max_requests -> blocked with retry_after
- Blocked identifier stays blocked for block_duration
- Different identifiers tracked independently
- Same identifier from different IPs tracked independently (composite key)
- cleanup() removes only expired entries
- Window resets after window_seconds elapsed
- Retry-after value is correct (remaining block time)
- Decorator extracts identifier from Request and raises HTTPException

**`common/tests/test_errors.py`** (5 tests)
- Each error subclass returns correct HTTP status code (400, 401, 403, 404, 409, 429, 500, 503)
- APIError injects request_id from context variable
- RateLimitError includes Retry-After in headers
- ErrorResponse Pydantic serialization matches API contract
- Details parameter (None vs dict) handled correctly

### API Gateway — 30 tests

**`api_gateway/tests/test_auth_security.py`** (12 tests)
- Correct password verifies against bcrypt hash
- Wrong password fails verification
- Corrupted hash returns False (fails safely as denial, does not raise)
- JWT token contains correct claims (user_id, org_id, role, exp)
- Expired token rejected
- Token with tampered signature rejected
- Token with missing required claims rejected
- TOTP code validates against secret
- TOTP replay (same code twice within window) rejected
- Org access: user can only get token for orgs they belong to
- Cross-org token rejected
- Refresh token rotation works (old token invalidated)

**`api_gateway/tests/test_encryption.py`** (8 tests)
- Encrypt then decrypt returns original plaintext
- Two encryptions of same plaintext produce different ciphertexts (unique nonce)
- Tampered ciphertext raises on decrypt
- Truncated ciphertext raises on decrypt
- Empty string encrypts/decrypts correctly
- Large payload (10KB) roundtrips
- Wrong key fails to decrypt
- Base64 encoding is valid (no raw bytes leaking)

**`api_gateway/tests/test_db_security.py`** (5 tests)
- encrypt_field/decrypt_field roundtrip for string values
- Unencrypted legacy value returned as-is (backward compat)
- Invalid/corrupted ciphertext returns fallback or raises
- None value handled (not encrypted)
- Different fields use independent encryption (no cross-contamination)

**`api_gateway/tests/test_api_key_security.py`** (5 tests)
- Key creation returns hash (not plaintext stored)
- Correct plaintext verifies against stored hash
- Wrong plaintext fails verification
- Key scoped to org_id only verifies within that org
- Revoked key fails verification

### Orchestration — 15 tests

**`test/adapter_test/test_injection_expanded.py`** (8 tests)
- Env var injection in job builder args (`${VAR}`, `$VAR`)
- Unicode homoglyph injection in model names
- Nested single+double quote escaping
- Null byte injection in artifact URIs
- Newline injection in config values
- YAML injection in Akash SDL builder
- Image name with tag injection (`image:tag;rm -rf /`)
- Volume mount path traversal (`../../etc/passwd`)

**`test/adapter_test/test_sdl_injection.py`** (7 tests)
- SDL template: resource limit values must be numeric
- SDL template: image name validated against allowlist pattern
- SDL template: environment variable values sanitized
- SDL template: port numbers validated (1-65535)
- SDL template: compute unit counts must be positive integers
- SDL template: nested YAML structure injection blocked
- SDL template: oversized SDL rejected

### Inference — 8 tests

**`inference/tests/test_upstream_security.py`** (8 tests)
- Auth token forwarded only to configured upstream hosts
- Timeout enforced (mock slow upstream -> timeout error)
- **[requires code change]** Host header injection in upstream URL blocked
- **[requires code change]** CRLF injection in custom headers blocked
- **[requires code change]** SSRF: private IP ranges (127.x, 10.x, 192.168.x) blocked in upstream URL
- **[requires code change]** Response headers from upstream not blindly forwarded (hop-by-hop stripped)
- **[requires code change]** TLS verification enabled by default for HTTPS upstreams
- **[requires code change]** Oversized response body rejected

### Guardrail — 7 tests

**`guardrail/tests/test_scan_endpoint_security.py`** (7 tests)
- POST /scan without API key -> 401
- POST /scan with invalid API key -> 403
- POST /scan with empty text -> 422 validation error
- POST /scan with oversized text (>1MB) -> 413 or validation error
- Error responses do not leak provider API keys or internal URLs
- CORS headers present and correct
- Health endpoint accessible without auth

### Data — 5 tests

**`data/tests/test_upload_security.py`** (5 tests)
- Filename with path traversal (`../../etc/passwd`) -> rejected
- Filename with null bytes -> rejected
- File extension not in whitelist -> rejected
- File exceeding size limit (50MB) -> rejected
- Empty file (0 bytes) -> rejected

---

## Layer 2: Error Handling Tests (~85 tests, ~1,800 LOC)

Focus: Fail-closed patterns, error sanitization, circuit breaker, graceful degradation.

### Common Module — 35 tests

**`common/tests/test_circuit_breaker.py`** (18 tests)
- Initial state is CLOSED
- Success in CLOSED state keeps CLOSED
- Single failure in CLOSED state stays CLOSED (below threshold)
- Failures at threshold -> state transitions to OPEN
- OPEN state rejects calls immediately with CircuitBreakerError
- After recovery_timeout in OPEN -> transitions to HALF_OPEN
- Single success in HALF_OPEN -> transitions to CLOSED (counters reset)
- Single failure in HALF_OPEN -> transitions back to OPEN
- Only expected_exception type counts as failure (others propagate but don't trip)
- Unexpected exception propagates without counting
- Decorator wraps async function correctly
- Decorated function passes args/kwargs through
- Registry.get_or_create returns same instance for same name
- Registry.get_or_create creates new instance for new name
- Registry.status() returns all breaker states
- Concurrent calls during HALF_OPEN: only one probe, rest rejected
- Failure count resets after successful call in CLOSED
- Recovery timeout precision (boundary: exactly at timeout vs 1ms before)

**`common/tests/test_exception_handlers.py`** (10 tests)
- APIError handler returns correct status_code and JSON body
- APIError handler includes request_id in response
- APIError handler includes custom headers (e.g., Retry-After)
- ValidationError handler returns 422 with field-level details
- ValidationError handler doesn't expose internal schema names
- Unhandled exception handler returns 500 with generic message
- Unhandled exception handler logs full traceback
- Unhandled exception handler returns empty details dict when debug=False
- Unhandled exception handler includes exception class name in details.type when debug=True
- register_exception_handlers installs all three handlers on app

**`common/tests/test_http_client.py`** (7 tests)
- Client created lazily on first request
- Subsequent requests reuse same client instance
- Request ID from context variable included in X-Request-ID header
- Internal API key included in X-Internal-API-Key header
- API key not present in logged error messages
- Timeout raises exception (not hang)
- close() marks client as closed, next request creates new client

### API Gateway — 20 tests

**`api_gateway/tests/test_gateway_error_handling.py`** (12 tests)
- Upstream returns 500 -> gateway returns sanitized 502
- Upstream timeout -> gateway returns 504
- Upstream connection refused -> gateway returns 502
- Upstream returns malformed JSON -> gateway returns 502
- Guardrail scan returns is_valid=False -> request blocked with 400
- Guardrail service unavailable -> request blocked (fail-closed)
- Quota exceeded -> 429 with Retry-After header
- Rate limit exceeded -> 429 with Retry-After header
- Concurrent requests with errors are isolated (no cross-contamination)
- Internal error messages never exposed in response body
- Request ID present in all error responses
- 401/403 errors don't reveal whether resource exists

**`api_gateway/tests/test_policy_engine_errors.py`** (8 tests)
- API key not found -> request denied
- API key found but org mismatch -> request denied
- Quota check with Redis unavailable -> fail-open (allow request, log warning)
- Usage tracking failure -> request still proceeds (non-blocking)
- Cache miss -> falls back to DB lookup
- Expired API key -> denied with clear message
- Concurrent quota checks don't double-count
- Policy evaluation with malformed config -> deny (not crash)

### Orchestration — 15 tests

**`test/model_deployment/test_controller_errors.py`** (8 tests)
- Deploy with missing required fields -> ValidationError
- Deploy with invalid artifact_uri -> rejected before provisioning
- Adapter provision raises exception -> deployment marked FAILED
- Adapter provision timeout -> deployment marked FAILED with timeout reason
- State transition FAILED->RUNNING -> rejected (invalid transition)
- Duplicate deploy request (same deployment_id) -> idempotent (returns existing)
- Delete non-existent deployment -> 404
- Controller handles DB connection error gracefully

**`test/adapter_test/test_adapter_error_handling.py`** (7 tests)
- Provider API timeout -> raises with clear message, no hang
- Provider auth failure (401) -> raises with "auth" in message, no key leaked
- Node not found during deprovision -> handled gracefully (not 500)
- Partial provision failure (some nodes ok, some fail) -> cleanup triggered for successful nodes
- Provider returns unexpected response format -> raises, doesn't crash
- Network error during reconcile -> logged, next reconcile proceeds
- Rate limited by provider -> backs off, returns retryable error

### Inference — 8 tests

**`inference/tests/test_service_error_handling.py`** (8 tests)
- Provider connection refused -> 502 Bad Gateway
- Provider timeout -> 504 Gateway Timeout
- Provider returns malformed JSON -> 500, generic message (not raw response)
- Provider returns 401 -> 502 (not 401 — don't proxy auth errors)
- Stream interrupted mid-response -> connection closed cleanly
- Rate limit per deployment enforced -> 429
- All providers unavailable -> 503 Service Unavailable
- Error log contains full provider response, API response contains only generic message

### Guardrail — 5 tests

**`guardrail/tests/test_engine_error_handling.py`** (5 tests)
- Provider init failure -> engine starts in degraded mode (other providers work)
- No providers available (all failed to init) -> returns is_valid=True (fail-open, current behavior)
- Provider exists but throws during scan -> returns is_valid=False (fail-closed)
- One provider fails, others succeed -> result aggregated from healthy providers
- Config update arrives mid-scan -> current scan completes with old config (no crash)

### Data — 3 tests

**`data/tests/test_engine_error_handling.py`** (3 tests)
- ChromaDB connection failure on init -> clear error message
- Malformed document (missing required fields) -> rejected with detail
- Retrieval from empty/non-existent collection -> empty result (not exception)

---

## Layer 3: Complex Business Logic Tests (~55 tests, ~1,200 LOC)

Focus: State machines, routing, scheduling, deployment lifecycle.

### Orchestration — 25 tests

**`test/model_deployment/test_worker_lifecycle.py`** (8 tests)
- Happy path: PENDING -> PROVISIONING -> DEPLOYING -> READY
- Provision failure: PENDING -> PROVISIONING -> FAILED
- Retry after transient failure: FAILED -> PENDING (if retries remaining)
- Node health check during deploy: unhealthy node -> revert and reprovision
- Worker shutdown mid-deployment -> state saved as PENDING (resumable)
- Deletion request during provisioning -> CANCELLING -> DELETED
- Deployment with 0 replicas -> rejected
- Concurrent deploy + delete for same deployment -> one wins, no corrupt state

**`test/placement_test/test_placement_scoring.py`** (6 tests)
- Node with more free GPU scores higher
- Node with sufficient memory but zero GPU -> excluded
- Multi-node ranking returns sorted list
- Tiebreaker: prefer node with fewer existing deployments
- Node with exactly enough resources -> included (boundary)
- All nodes insufficient -> empty result (not exception)

**`test/compute_pools_nodes/test_pool_manager.py`** (6 tests)
- Create pool -> persisted with correct fields
- Bind provider resource to pool -> resource appears in pool inventory
- Unbind resource -> resource removed from pool
- List pool inventory with filters -> correct subset returned
- Delete pool with active nodes -> blocked with error
- Delete empty pool -> succeeds

**`test/autoscaler/test_autoscaler_logic.py`** (5 tests)
- Utilization above threshold -> scale-up triggered
- Utilization below threshold for cooldown duration -> scale-down triggered
- Utilization fluctuates within cooldown -> no scale action
- Scale-up respects max_replicas bound
- Scale-down respects min_replicas bound

### API Gateway — 12 tests

**`api_gateway/tests/test_inference_routing.py`** (7 tests)
- Request routed to correct deployment by model ID
- Guardrail scan blocks request -> 400 with violation details
- Prompt template applied before forwarding to provider
- Streaming response passed through with correct SSE format
- Preferred deployment unavailable -> fallback to next available
- No deployments available for model -> 503
- Request with API key quota tracking -> usage incremented

**`api_gateway/tests/test_deployment_management.py`** (5 tests)
- Create deployment -> default rate limit policy attached
- List deployments -> returns only caller's org deployments
- Delete deployment -> orchestration notified, DB record cleaned
- Duplicate deployment name within org -> 409 Conflict
- Create deployment with invalid model config -> 400

### Inference — 8 tests

**`inference/tests/test_routing_logic.py`** (5 tests)
- Provider selected based on deployment configuration
- Load balanced across healthy replicas (round-robin or least-connections)
- Unhealthy replica skipped automatically
- All replicas unhealthy -> 503
- Concurrent request limit per deployment enforced

**`inference/tests/test_stream_processor.py`** (3 tests)
- SSE chunks parsed correctly (data: prefix, double newline delimiter)
- Partial chunk buffered until complete
- Stream cancellation by client -> upstream connection closed

### Data — 7 tests

**`data/tests/test_chunker.py`** (4 tests)
- Splits on separator hierarchy (paragraph -> sentence -> word)
- Overlap region contains expected repeated content
- Single chunk larger than max_size -> force-split at max_size
- Empty text -> empty list (not exception)

**`data/tests/test_prompt_engine.py`** (3 tests)
- Token count under budget -> returns True
- Token count over budget -> returns False
- Unknown model name -> falls back to default encoder (cl100k_base)

### Guardrail — 3 tests

**`guardrail/tests/test_engine_orchestration.py`** (3 tests)
- Two providers return violations -> both merged in result
- Provider disabled via config -> not called
- Custom keywords passed through to provider scan call

---

## Implementation Sequence

1. **Shared fixtures** — create/extend conftest.py files (all services)
2. **Layer 1: Security** — ~90 tests across 13 test files
3. **Layer 2: Error Handling** — ~85 tests across 11 test files
4. **Layer 3: Complex Logic** — ~55 tests across 11 test files

Each layer is one or more PRs. Tests added to `.github/workflows/test.yml` after each layer.

**CI workflow note:** The current workflow enumerates test files individually (18 files). With ~35 new test files, consider switching to directory-based discovery (`pytest package/src/inferia/ -p no:twisted -p no:trio -p no:tornasync`) after Layer 3 is complete.

## Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Total test functions | ~132 | ~362 |
| Estimated coverage | ~15% | ~40-50% |
| Security-critical code covered | ~20% | ~85% |
| Error handling paths covered | ~10% | ~70% |
| Complex logic covered | ~15% | ~60% |
