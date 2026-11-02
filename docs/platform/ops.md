# Platform operations runbook

## Evidence boundary

This runbook covers CPU-verifiable control-plane operations. GPU-engine
calibration and measured capacity numbers are separate lease work; see the
[capacity report](capacity/report.md).

## Database backup and restore

Enable the Helm backup job only with a durable PVC:

```bash
helm upgrade --install inferiallm deploy/helm/inferiallm \
  --set backup.enabled=true \
  --set backup.existingClaim=inferiallm-backups
```

The job emits custom-format `pg_dump` files named
`inferiallm-<UTC timestamp>.dump`; `backup.retentionDays` controls deletion.
The chart intentionally refuses to enable a backup without `existingClaim`.

### Restore drill

1. Put the application in maintenance mode or scale its Deployment to zero.
2. Create a one-off PostgreSQL client pod with the backup PVC mounted.
3. Verify the archive before changing the database:

   ```bash
   pg_restore --list /backup/inferiallm-<timestamp>.dump | head
   ```

4. Restore into an empty target database, then run the chart migration hook:

   ```bash
   pg_restore --clean --if-exists --no-owner \
     --dbname "$DATABASE_URL" /backup/inferiallm-<timestamp>.dump
   helm upgrade inferiallm deploy/helm/inferiallm --reuse-values --wait
   ```

5. Check `/api/health`, sign in with a non-admin account, and inspect a known
   deployment and audit record. Record the archive timestamp, restore timing,
   and result in the incident log.

Never test a restore against the production database in place; use a fresh
database/PVC or a clearly isolated namespace.

## Usage-stream outage

Inference writes usage events to the Redis Stream `inferia:usage:v1`. The API
gateway worker persists a batch through `UsageSink` and only then ACKs it. On
restart it drains its pending entries before new messages; the delivery model
is at-least-once. If Redis is unavailable, inference falls back to the
internal gateway HTTP usage endpoint.

Check stream health:

```bash
redis-cli XINFO GROUPS inferia:usage:v1
redis-cli XPENDING inferia:usage:v1 api-gateway-usage
```

Investigate growing pending counts before deleting any stream entries. A
consumer restart is safe; unacknowledged events are deliberately replayed.

### Local Redis validation

An isolated Redis 7 container on `127.0.0.1:56379` was used to verify the
actual inference producer on 2026-11-02. The following published one event to
`inferia:usage:v1`; `XRANGE` showed the expected `user_id`, model and token
counts:

```bash
REDIS_PORT=56379 PYTHONPATH=src python -c '
import asyncio
from inference.core.usage_events import publish_usage_event
assert asyncio.run(publish_usage_event("integration-user", "integration-model", {
    "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
}))'
```

The full llm-d kind demo additionally requires an existing kind cluster and
Kubernetes context; neither is created implicitly by this runbook.

### Local Redis-to-Postgres validation

On 2026-11-02, the same isolated Redis container and the local
`inferia_test` Postgres database were used to run the actual stream consumer.
Two events were persisted to `usage_stats` with their expected request and
token counters, then `XPENDING inferia:usage:v1 api-gateway-usage` reported
zero pending messages. This validates producer → Redis Stream → batch worker
→ Postgres → ACK on local infrastructure. The worker also has a regression
test for Redis's empty pending-entry frame, which must transition the stable
consumer from replay mode to fresh-message reads.

## GPU lease handoff

Run the CPU-safe rehearsal first:

```bash
bash bench/lease/run_lease.sh --dry-run --base-url http://127.0.0.1:8000
```

Use `--execute` only on the confirmed GPU host. Preserve the generated
`manifest.env` and `benchmark.log` beside results; label them **MEASURED** only
after a real-engine run completes.
