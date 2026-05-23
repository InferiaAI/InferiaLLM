# AWS EC2 worker provisioning — design spec

**Date:** 2026-05-20
**Repos touched:** `InferiaLLM`, `inferia-worker`
**Status:** approved (brainstorming complete)

## Goal

Provision real AWS EC2 GPU instances from the InferiaLLM control plane,
auto-install `inferia-worker` on them via cloud-init pulling from GHCR, and
have the worker register itself into the control plane and start serving
inference — all without any operator step on the instance side.

ECS is explicitly out of scope for this iteration (Fargate has no GPU,
ECS-on-EC2 buys no capability over raw EC2). Future work.

## Non-goals

- ECS / EKS / Fargate (deferred).
- Multi-region failover, spot-fleet bidding, auto-scaling groups (this spec
  provisions one instance per `provision_node` call; ASG/SpotFleet are
  future work).
- ECR mirroring of the worker image (GHCR public is sufficient).
- Live AWS integration tests in CI (boto3 is mocked end-to-end).

## High-level architecture

```
InferiaLLM control plane                EC2 instance (g5/g4dn/p4d/p5)
─────────────────────────              ─────────────────────────────────────
AWSAdapter.provision_node              cloud-init (user-data) runs:
  ├─ mint_bootstrap_token(pool)          1. install Docker + nvidia-container
  ├─ build_user_data(token, …)              -toolkit if absent
  └─ ec2.run_instances                   2. docker run -d --restart=always
       tags:                                    --gpus=all
         InferiaBootstrapId=<bid>              -v /var/run/docker.sock:…
         InferiaPoolId=<pool>                  -v /var/lib/inferia-worker:…
         InferiaOrgId=<org-uuid>               --network host
       user_data=<script>                      -e BOOTSTRAP_TOKEN=…
                                               -e CONTROL_PLANE_URL=…
wait_for_ready polls                          -e NODE_NAME=i-…
compute_inventory until                       -e POOL_ID=…
worker registers itself                       ghcr.io/<org>/inferia-worker:vX

worker /v1/workers/register   ◀───── cloudenv.Detect() (IMDSv2)
  validates bootstrap_token            POST {bootstrap_token, allocatable,
  consumes row (atomic UPDATE)               runtime_env: "aws-ec2",
  mints WorkerJWT                            instance_id, region, availability_zone}
  records labels.runtime_env etc.

worker opens /v1/workers/channel
  Authorization: Bearer <WorkerJWT>
  Hello frame carries cloud env again
  (truth source on every reconnect)
```

## Key invariants

1. The CP↔AWS interaction stops at `RunInstances`. From there, the worker's
   existing bootstrap flow takes over — there is **no** AWS-specific code
   anywhere in `control/`, `runtime/`, `admin/`, `inference/` packages.
2. Runtime-env metadata flows **worker → CP**, never CP → worker. The
   worker probes IMDS locally; CP just records what it's told.
3. boto3 credential resolution is lazy: `boto3.Session()` with no args
   picks up an instance IAM role; with explicit kwargs it uses
   per-pool encrypted credentials. The "auto-detect" decision is
   one `if provider_credential_name:` branch in the adapter.
4. The bootstrap token is the only secret in user-data. Image, CP URL,
   pool id, node name are non-sensitive config.

## Configuration

### Control-plane (env / `inferiallm.yaml`)

- `INFERIA_WORKER_IMAGE` — default `ghcr.io/inferiaai/inferia-worker`. The org segment is parameterized so other orgs can ship private builds. Published by `InferiaAI/inferia-worker`'s `docker-publish.yml` workflow on every `v*` tag. Used as the default in `bootstrap_builder.build_user_data`.
- `INFERIA_WORKER_IMAGE_TAG` — default `latest`. Per-pool override via `pool.metadata.worker_image_tag` to pin a specific version.
- `INFERIA_BOOTSTRAP_TOKEN_TTL_SECONDS` — default `3600`. Raise to `7200` if cloud-init reliably exceeds 1h on cold AMIs in some region.

### Per-pool (`compute_pool.metadata` jsonb, operator-set)

- `subnet_id` (required) — VPC subnet for `RunInstances`.
- `security_group_ids` (required) — list of SG IDs allowing egress to the control plane + ingress on the worker port from the CP.
- `iam_instance_profile` (optional) — ARN; needed only if the worker should call AWS APIs from the instance (e.g. future S3 model artifact pulls).
- `ami_id` (optional) — overrides the default DLAMI Ubuntu lookup.
- `worker_image_tag` (optional) — overrides the CP-wide default tag.
- `root_volume_gb` (optional, default `100`) — EBS gp3 root disk size.

## Components

### InferiaLLM (Python)

#### `services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py`

Full rewrite of the stub.

- `_session(credential_name)` — builds & caches `boto3.Session`. With no
  name → default chain (picks up instance role); with a name → loads from
  `provider_credentials` table, decrypts via Fernet, builds session with
  explicit `aws_access_key_id` / `aws_secret_access_key` kwargs.
- `discover_resources(region)` — `ec2.describe_instance_types` filtered to
  GPU families (g5.*, g4dn.*, p4d.*, p5.*). Fixes `.ge` → `.get` typo.
  Pricing from a static fallback map (live pricing API is future work).
- `provision_node(provider_resource_id, pool_id, region, use_spot,
  metadata, provider_credential_name)` —
    1. Resolves AMI: pool's `metadata.ami_id` if set; else DLAMI Ubuntu
       lookup by region (`ssm.get_parameter` on the official DLAMI alias).
    2. Mints bootstrap token (`worker_controller.auth.mint_bootstrap_token`).
    3. Builds user-data via `bootstrap_builder.build_user_data`.
    4. Calls `ec2.run_instances`:
        - `InstanceType = provider_resource_id`
        - `ImageId = <resolved>`
        - `MinCount = MaxCount = 1`
        - `IamInstanceProfile = pool.metadata.iam_instance_profile` (optional)
        - `SecurityGroupIds = pool.metadata.security_group_ids`
        - `SubnetId = pool.metadata.subnet_id`
        - `BlockDeviceMappings = [{root EBS gp3 100GB by default}]`
        - `TagSpecifications = [{ResourceType:"instance", Tags:[
            {Name}, {InferiaBootstrapId}, {InferiaPoolId}, {InferiaOrgId}
          ]}]`
        - `UserData = <script>` (boto3 base64-encodes for us)
        - `InstanceMarketOptions = {MarketType:"spot"}` when `use_spot=True`
    5. Writes `compute_inventory` row: `state="provisioning"`,
       `provider_instance_id=i-…`, `labels.bootstrap_id=<bid>`.
    6. Returns `{provider_instance_id, hostname, gpu_total, vcpu_total,
       ram_gb_total, region, node_class, metadata: {bootstrap_id}}`.
    7. On any boto3 error: roll back bootstrap token row (delete), do not
       insert `compute_inventory` row, raise `ProvisionError(detail)` —
       internal exception text never reaches the API consumer.
- `wait_for_ready(provider_instance_id, timeout=900)`:
    1. `ec2.get_waiter("instance_running")` to confirm hypervisor boot.
    2. Poll `compute_inventory` by bootstrap_id (in `labels`) every 5s
       until `state="ready"` or timeout.
    3. On timeout: call `deprovision_node` (best-effort), raise
       `ProvisionTimeoutError`.
    4. Returns `"ready"`.
- `deprovision_node(provider_instance_id, provider_credential_name)`:
  `ec2.terminate_instances`. Treats `InvalidInstanceID.NotFound` as success.
- `get_logs(provider_instance_id, …)`: `ec2.get_console_output` (cloud-init
  log; container logs come via the worker admin WS once registered).
- `get_log_streaming_info(...)`: returns `{supported: True, kind:
  "worker-ws", ws_url: <admin_workers_logs_url>}` once a node_id exists
  for this instance; otherwise `{supported: False, reason: "not registered"}`.

#### `services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py` (new)

```python
def build_user_data(
    *,
    bootstrap_token: str,
    control_plane_url: str,
    node_name: str,
    pool_id: str,
    image: str = "ghcr.io/<org>/inferia-worker",
    image_tag: str = "latest",
) -> str:
    """Render a bash user-data script. All interpolated values pass through
    shlex.quote. Returns a string <= 16384 bytes."""
```

Script skeleton:
```bash
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/inferia-bootstrap.log) 2>&1

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

if ! command -v nvidia-ctk >/dev/null && lspci | grep -qi nvidia; then
  distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update && apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

mkdir -p /var/lib/inferia-worker
docker pull <IMAGE>:<TAG>
docker rm -f inferia-worker 2>/dev/null || true
docker run -d --name inferia-worker --restart=always \
  $(lspci | grep -qi nvidia && echo "--gpus=all") \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/inferia-worker:/var/lib/inferia-worker \
  --network host \
  -e BOOTSTRAP_TOKEN=<TOKEN_Q> \
  -e CONTROL_PLANE_URL=<CP_URL_Q> \
  -e NODE_NAME=<NODE_NAME_Q> \
  -e POOL_ID=<POOL_ID_Q> \
  <IMAGE_Q>:<TAG_Q>
```

All `<…_Q>` values are `shlex.quote`'d in Python before substitution.
The function REJECTS inputs containing `\x00` or values longer than 1024
chars before quoting.

#### `services/orchestration/services/worker_controller/auth.py` (extend)

```python
def mint_bootstrap_token(
    pool_id: str, org_id: str, ttl_seconds: int = 3600
) -> tuple[str, str]:
    """Generate a 32-byte URL-safe token and a bootstrap_id.
    Store SHA-256 hash + metadata in worker_bootstrap_tokens.
    Return (plaintext_token, bootstrap_id)."""

async def consume_bootstrap_token(
    token: str, conn: asyncpg.Connection
) -> BootstrapClaim:
    """Atomic SQL:
        UPDATE worker_bootstrap_tokens
        SET consumed_at = NOW()
        WHERE token_hash = $1
          AND consumed_at IS NULL
          AND expires_at > NOW()
        RETURNING bootstrap_id, pool_id, org_id
    Returns the claim or raises InvalidBootstrapToken."""
```

Token format: `secrets.token_urlsafe(32)`. Hash: `hashlib.sha256`.
Storage: `worker_bootstrap_tokens (id uuid pk, token_hash text unique,
pool_id uuid, org_id uuid, expires_at timestamptz, consumed_at timestamptz
null, created_at timestamptz default now())`.

#### `services/orchestration/api/workers.py` (extend `/v1/workers/register`)

Accept body fields:
- `bootstrap_token` (str, optional) **OR** existing `worker_jwt` flow
- `node_name` (str, required)
- `pool_id` (uuid, required)
- `allocatable` (dict, required) — cpu / mem / gpu / disk
- `runtime_env` (str, optional, max 64 chars) — e.g. `"aws-ec2"`, `"local"`
- `instance_id` (str, optional, max 128 chars)
- `region` (str, optional, max 64 chars)
- `availability_zone` (str, optional, max 64 chars)

Logic:
1. If `bootstrap_token` present: `await consume_bootstrap_token(token, conn)`
   — on failure, return 401 with `detail: invalid_bootstrap_token`.
2. Match `pool_id` against `claim.pool_id`; mismatch → 401 `pool_scope_violation`.
3. INSERT/UPDATE `compute_inventory`: `node_id = generated`, `state = "ready"`,
   `provider_instance_id = instance_id`, `labels = {runtime_env, region, availability_zone,
   bootstrap_id}` merged with whatever the adapter already wrote.
4. Mint long-lived `WorkerJWT` (existing helper).
5. Return `{node_id, worker_jwt}`.

#### `infra/schema/migrations/<next>_worker_bootstrap_tokens.sql` (new)

```sql
CREATE TABLE IF NOT EXISTS worker_bootstrap_tokens (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash        text NOT NULL UNIQUE,
  pool_id           uuid NOT NULL REFERENCES compute_pool(id) ON DELETE CASCADE,
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  expires_at        timestamptz NOT NULL,
  consumed_at       timestamptz NULL,
  consumed_node_id  uuid NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_worker_bootstrap_tokens_pool ON worker_bootstrap_tokens(pool_id);
```

#### `common/runtime_env.py` (new)

Small helper for CP-side telemetry only. Not load-bearing.

```python
def detect_runtime_env() -> Literal["local","aws-ec2","k8s","unknown"]:
    """INFERIA_RUNTIME_ENV env wins; else IMDSv2 probe with 200ms timeout;
    else 'local'. Cached after first call."""
```

### inferia-worker (Go)

#### `internal/cloudenv/detect.go` (new)

```go
type Kind string
const (
    KindLocal   Kind = "local"
    KindAWSEC2  Kind = "aws-ec2"
    KindUnknown Kind = "unknown"
)

type RuntimeInfo struct {
    Kind             Kind   `json:"runtime_env"`
    InstanceID       string `json:"instance_id,omitempty"`
    Region           string `json:"region,omitempty"`
    AvailabilityZone string `json:"availability_zone,omitempty"`
}

// Detect probes IMDSv2; env vars override individual fields.
// Total budget: 200ms. Cached for process lifetime.
func Detect() RuntimeInfo
```

IMDSv2 flow:
```
PUT http://169.254.169.254/latest/api/token
  X-aws-ec2-metadata-token-ttl-seconds: 60        → <token>
GET http://169.254.169.254/latest/dynamic/instance-identity/document
  X-aws-ec2-metadata-token: <token>               → JSON {instanceId, region, availabilityZone, …}
```

Env overrides:
- `INFERIA_RUNTIME_ENV` — sets Kind directly (skips IMDS).
- `INFERIA_INSTANCE_ID` / `INFERIA_REGION` / `INFERIA_AZ` — override
  individual fields.

#### `internal/control/bootstrap.go` (extend)

Add `runtime_env`, `instance_id`, `region`, `availability_zone` to the
register POST body. Sourced from `cloudenv.Detect()`. JSON `omitempty` for
all four so the request stays clean on non-cloud workers.

#### `internal/control/protocol.go` (extend `HelloBody`)

Add the same four fields. CP refreshes `compute_inventory.labels` on each
attach/reconnect — single truth source.

#### `cmd/worker/main.go` (wire)

Call `cloudenv.Detect()` once at startup; thread the result into both the
bootstrap call and the channel's Hello builder.

### inferia-worker CI

#### `.github/workflows/docker-publish.yml` (new)

- Triggers: `push: tags: ["v*"]` + `workflow_dispatch`.
- Permissions: `contents: read`, `packages: write`.
- Login: `docker/login-action@v3` against `ghcr.io` with
  `${{ secrets.GITHUB_TOKEN }}` (no PAT needed for org-owned packages).
- Metadata: `docker/metadata-action@v5`, images
  `ghcr.io/${{ github.repository_owner }}/inferia-worker`,
  tags: `{{version}}`, `{{major}}.{{minor}}`, `latest` on release event.
- Build/push: `docker/build-push-action@v5` with platforms
  `linux/amd64,linux/arm64`.

Worker is currently amd64-only in `Dockerfile` (`GOARCH=amd64` hard-coded);
spec includes flipping that to `GOARCH=${TARGETARCH}` so multi-arch buildx
works. arm64 builds are exercised on PRs as `--push=false` dry-runs.

#### `.github/workflows/test.yml` (new)

- Triggers: `pull_request`, `push: branches: [main]`.
- Steps: setup-go 1.26, `go vet ./...`, `go test ./... -race -cover`,
  `go build ./...`.
- Coverage gate: ≥95% on new files (`internal/cloudenv`,
  added paths in `internal/control`).

## Data flow (one provision, end-to-end)

See full sequence diagram in section 3 of the brainstorming transcript.
Summary:

1. Dashboard → gateway → orchestration `ModelDeploymentWorker.handle_deploy_requested`.
2. Placement: no ready nodes in pool → `AWSAdapter.provision_node(pool)`.
3. Adapter mints bootstrap token, builds user-data, calls `RunInstances`,
   writes `compute_inventory` row in `state="provisioning"`.
4. EC2 cloud-init installs Docker + nvidia-container-toolkit, `docker run`s
   the worker image with the bootstrap token in env.
5. Worker boots, runs `cloudenv.Detect()` (IMDSv2), POSTs
   `/v1/workers/register` with token + cloud-env fields.
6. CP atomic-consumes the token, mints `WorkerJWT`, flips inventory row to
   `state="ready"`, returns `{node_id, worker_jwt}` to the worker.
7. Worker persists JWT, dials `/v1/workers/channel`, sends Hello with
   cloud-env fields.
8. `wait_for_ready` (still polling inventory) sees `state="ready"`, returns.
9. `WorkerDeploymentStrategy.deploy` proceeds normally: `controller.load_model`
   → `LoadModel` envelope → worker runtime → docker pull/run → readiness →
   `CommandResult{ok, endpoint}` → deployment `RUNNING`.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| `RunInstances` rejects (quota, AMI, subnet) | boto3 ClientError | rollback bootstrap-token row (delete), no inventory row, raise `ProvisionError`, deployment FAILED |
| Instance boots but cloud-init fails | `wait_for_ready` polls timeout (default 15min) | `TerminateInstances`, delete bootstrap-token row, deployment FAILED |
| Bootstrap token already consumed (race / replay) | atomic UPDATE returns 0 rows | 401 to worker; cloud-init logs the failure to console-output; operator must re-provision |
| Worker registers, then WS channel drops mid-LoadModel | existing dedup TTL (5min) + reconnect backoff | LoadModel envelope id prevents double-pull on reconnect; already covered by `channel.go` |
| Operator pre-terminates instance via AWS console | next heartbeat missed; CP marks `state="unreachable"` after 3 missed beats | `deprovision_node` is idempotent; terminate of gone instance is a no-op |
| Bootstrap token expires before instance boots (cold AMI pull) | atomic UPDATE rejects expired row | manual re-provision; consider raising default TTL to 2h |
| User-data shell injection via metadata | `shlex.quote` on every interpolation; input validation rejects nulls / oversized strings | tests verify quoting under adversarial inputs |
| IMDSv2 unreachable on a non-AWS host | 200ms timeout, returns `Kind="local"` | worker proceeds with no cloud-env fields in the register body |

## Testing strategy

Both repos target ≥95% coverage on new files. Existing files untouched
unless changed by this spec.

### InferiaLLM (pytest + pytest-asyncio)

- `adapter_engine/adapters/aws/tests/test_aws_adapter.py`
  - discover_resources: normal, empty, AWS error
  - provision_node: happy, user-data size ≤ 16KB even with max inputs,
    user-data shell-safety against adversarial pool/node names, credential
    resolution (instance role and stored), credential decrypt failure,
    RunInstances rejection rolls back bootstrap token, AMI not found
  - wait_for_ready: polls until ready, timeout terminates instance
  - deprovision_node: happy, instance already gone
- `adapter_engine/adapters/aws/tests/test_bootstrap_builder.py`
  - minimal happy script, shell-injection resistance (quotes, `;`, `$()`,
    backticks, newlines, NUL), size ≤ 16384 bytes with max-length inputs,
    idempotent Docker install, GPU flags present on GPU instances
- `services/worker_controller/tests/test_bootstrap_tokens.py`
  - mint uniqueness, hash-not-plaintext storage, consume happy, double-use
    rejected, expired rejected, unknown rejected, race (asyncio.gather x2 →
    one wins), pool-scope violation rejected, register extra fields
    recorded, register without extra fields back-compat, oversize fields
    rejected
- `common/tests/test_runtime_env.py`
  - env override wins, IMDS probe timeout doesn't hang, unreachable returns
    `"local"`

### inferia-worker (`go test`)

- `internal/cloudenv/detect_test.go`
  - env override skips network, IMDSv2 success via httptest, IMDS
    unreachable returns local, IMDSv1-only host returns local, cached
    after first call, env field overrides individual IMDS fields,
    oversize IMDS payload bounded (no OOM)
- `internal/control/bootstrap_test.go` (extend)
  - register body includes cloud-env when detected
  - register body omits cloud-env on local
- `internal/control/channel_test.go` (extend)
  - Hello frame carries cloud-env after reconnect

### What is NOT tested

- Live AWS API calls (boto3 mocked at client level; no `moto`).
- Live IMDS (httptest fakes it).
- Live GHCR push from PRs (only on tag push).
- IMDSv1 (deprecated; we require v2-capable instances).

## Open / deferred

- ECS / EKS / Fargate.
- Spot fleets and ASG-based pools.
- Live pricing via the AWS Pricing API (static fallback for now).
- ECR mirroring of the worker image (GHCR public for now).
- Worker image signing (cosign) — deferred to a separate hardening pass.
- Multi-region failover.

## References

- Existing adapter base: `services/orchestration/services/adapter_engine/base.py`
- Existing worker bootstrap: `inferia-worker/internal/control/bootstrap.go`
- Existing register endpoint: `services/orchestration/api/workers.py`
- Worker channel protocol: `inferia-worker/internal/control/protocol.go`
- Existing CI to mirror: `InferiaLLM/.github/workflows/docker-publish.yml`
