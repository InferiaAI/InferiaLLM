# Deployment Terminal Log Persistence

**Issue:** #169 — Persist terminal logs on deployment failures/stop
**Date:** 2026-04-01

## Problem

Terminal logs (deployment startup/runtime output) are currently only available via live WebSocket streams from providers (Nosana, SkyPilot, K8s, etc.). The frontend keeps a 2000-line in-memory buffer. Once the WebSocket disconnects or the deployment fails/stops, those logs are lost — there is no persistent storage in InferiaLLM.

## Decisions

| Decision | Choice |
|----------|--------|
| Storage backend | Elasticsearch |
| Capture method | Sniff from existing WebSocket relay (Approach A) |
| Line cap | 10,000 lines per deployment |
| Capture timing | Continuously during streaming (real-time) |
| Consumer | Dashboard only (no new REST API for programmatic access) |
| ES dependency | Best-effort — degrade gracefully if ES is unavailable |

## Architecture

### 1. Log Capture (Backend)

In `deployment_server.py`'s `websocket_logs_endpoint()`, the relay loop already receives each log message from the provider and forwards it to the client. A new **`DeploymentLogBuffer`** class:

- Sits alongside the relay loop — each message relayed also gets appended to the buffer
- Maintains a **circular buffer of 10,000 lines** (`collections.deque` with `maxlen`)
- **Flushes to Elasticsearch** in two scenarios:
  - **Periodic flush** — every ~10 seconds via an asyncio background task running alongside the relay
  - **Final flush** — when the WebSocket disconnects (client closes, provider disconnects, or deployment fails/stops)
- Each flush bulk-indexes the buffered lines to ES and clears the buffer
- Index pattern: `inferia-deployment-logs-{YYYY.MM.dd}`
- Document fields: `deployment_id`, `org_id`, `timestamp`, `line_number`, `message`

If ES is unavailable, the flush logs a warning and discards — no impact on the relay.

### 2. Elasticsearch Integration

A new **`DeploymentLogStore`** utility class in the orchestration service:

- Initializes an async ES client (`AsyncElasticsearch`) using existing `ELASTICSEARCH_URL` config
- **`flush(deployment_id, org_id, lines)`** — bulk-indexes a batch of log lines with: `deployment_id`, `org_id`, `timestamp`, `line_number` (global sequence per deployment), `message`
- **`get_logs(deployment_id, limit=10000)`** — queries ES by `deployment_id`, sorted by `line_number` ascending, returns the stored lines
- **Connection check on init** — pings ES, sets an `available` flag. All operations short-circuit with a warning log if `available=False`
- No index template management — relies on ES dynamic mapping (fields are simple: keyword, integer, text, date)

Config additions to orchestration's `config.py`:
- `ELASTICSEARCH_URL` (optional, default `None`)
- `DEPLOYMENT_LOG_BUFFER_SIZE` (default `10000`)
- `DEPLOYMENT_LOG_FLUSH_INTERVAL` (default `10` seconds)

### 3. Frontend Fallback

In `TerminalLogs.tsx`, when the live WebSocket stream is unavailable (deployment is in `TERMINATED`/failed state, or the WS connection fails to establish):

- Instead of showing an empty terminal, call the existing `GET /deployment/logs/{deploymentId}` endpoint
- The backend handler for that endpoint gets a new code path: if the provider's live logs are unavailable, fall back to querying ES via `DeploymentLogStore.get_logs(deployment_id)`
- The returned logs are rendered in the same terminal UI — same ANSI stripping, same formatting
- A subtle indicator (e.g., "Showing saved logs" label) distinguishes historical logs from a live stream

No new endpoints needed — the existing `/deployment/logs/{deploymentId}` REST endpoint already serves as the non-streaming log fetcher. The ES fallback is added inside it.

### 4. Error Handling & Edge Cases

- **ES unavailable at startup:** `DeploymentLogStore` sets `available=False`, all flushes are no-ops with a warning log. The relay and live streaming work exactly as today.
- **ES becomes unavailable mid-stream:** Flush catches the exception, logs a warning, and continues. Buffered lines that failed to flush remain in the deque and are retried on the next flush cycle.
- **Multiple clients watching same deployment:** Each WebSocket session has its own buffer instance. Duplicate writes are handled by using `{deployment_id}-{line_number}` as the ES document `_id` — duplicate writes are idempotent upserts. `line_number` is a monotonic counter starting from the highest existing line in ES for that deployment (queried once on buffer init), ensuring consistency across sessions.
- **Client disconnects mid-stream:** The final flush fires in the `finally` block of the relay loop, persisting whatever is in the buffer at that point.
- **Very fast log output:** The 10k-line deque naturally drops oldest lines. Flush interval of 10s keeps ES write pressure manageable.
- **Log cleanup:** No automatic retention policy in this iteration. ES indices follow the daily pattern and can be managed with ILM (Index Lifecycle Management) later if needed.

## Files to Modify

| File | Change |
|------|--------|
| `package/src/inferia/services/orchestration/services/model_deployment/deployment_server.py` | Add buffer sniffing in WebSocket relay loop, ES fallback in `get_deployment_logs()` |
| `package/src/inferia/services/orchestration/config.py` | Add `ELASTICSEARCH_URL`, `DEPLOYMENT_LOG_BUFFER_SIZE`, `DEPLOYMENT_LOG_FLUSH_INTERVAL` |
| `apps/dashboard/src/components/deployment/TerminalLogs.tsx` | Add ES fallback when live stream is unavailable, "Showing saved logs" indicator |

## New Files

| File | Purpose |
|------|---------|
| `package/src/inferia/services/orchestration/services/model_deployment/log_store.py` | `DeploymentLogStore` (ES client wrapper) and `DeploymentLogBuffer` (circular buffer + flush logic) |
