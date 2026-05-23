# AWS provisioning via Pulumi — design spec

**Date:** 2026-05-22
**Repo:** InferiaLLM
**Status:** approved (brainstorming complete)

## Goal

Replace SkyPilot with Pulumi as the cloud provisioning engine. For this
iteration AWS is the first-class implementation; GCP and Azure get
matching Pulumi adapters built on the same shared base. Each AWS pool is
a Pulumi stack persisted to a local-filesystem state backend on the
orchestration container. The `inferia-worker` Docker image published to
GHCR (`ghcr.io/inferiaai/inferia-worker:0.1.0`) runs on every provisioned
EC2 instance via cloud-init.

The existing UI flow (Settings → Providers → AWS for credentials and
account-wide provisioning defaults; Compute → New Pool to create a pool)
keeps working — only the IaC tool behind the curtain changes.

## Non-goals

- Lambda Cloud and Runpod adapters. SkyPilot supports those via REST APIs;
  Pulumi has no official providers. Drop them entirely until someone writes
  Pulumi `dynamic.ResourceProvider`s wrapping their HTTP APIs.
- Multi-replica orchestration. Local-FS Pulumi state means one orchestration
  container owns all stacks. S3 state backend is a follow-up.
- Pulumi Cloud / SaaS state hosting. Local-FS only.
- Encrypted Pulumi config secrets. We resolve AWS credentials per-call as
  env vars and never persist them to stack files.
- Schema migrations. `compute_pools.metadata` jsonb is reused as-is; no
  new columns.

## Scope summary

### Backend

1. New `services/orchestration/services/adapter_engine/adapters/pulumi/`
   subtree with three adapters (AWS, GCP, Azure) on a shared base.
2. Delete `adapters/skypilot/` and the unregistered `adapters/aws/aws_adapter.py`
   (boto3 adapter from a prior iteration). Keep `adapters/aws/bootstrap_builder.py`
   and `adapters/aws/pool_metadata.py` — both reused by `PulumiAWSAdapter`.
3. Rewrite `adapter_engine/registry.py` — drop SkyPilot, register the three
   Pulumi adapters. `lambda` and `runpod` become unregistered.
4. Replace SkyPilot dependency in `pyproject.toml` with Pulumi packages.
5. Settings: add `pulumi_state_dir` (default `/var/lib/inferia/pulumi-state`),
   `pulumi_passphrase` (default empty — only used if/when secrets layer is enabled).

### Frontend

6. `pages/Settings/Providers/ProviderList.tsx` — AWS description updates
   from "EC2 GPU clusters via SkyPilot" to "EC2 GPU clusters via Pulumi";
   remove Lambda/Runpod cards if present.
7. `pages/Compute/NewPool.tsx` — copy that mentions SkyPilot ("SkyPilot
   Configuration", "SkyPilot defaults") changes to "Cluster Configuration"
   / "Pulumi defaults".

### Worker repo (`inferia-worker`)

8. Unchanged. The GitHub Action already publishes
   `ghcr.io/inferiaai/inferia-worker:0.1.0`. `PulumiAWSAdapter` embeds that
   image URL in cloud-init user-data via the existing `bootstrap_builder`.

## Architecture

```
InferiaLLM control plane (single container)
└── orchestration service
    └── adapter_engine/
        ├── registry.py            # registers PulumiAWSAdapter, …GCP…, …Azure…
        └── adapters/
            ├── pulumi/
            │   ├── base.py        # PulumiProvisioningBase
            │   ├── credentials.py # ProvidersConfig → env vars
            │   ├── pulumi_aws_adapter.py
            │   ├── pulumi_gcp_adapter.py
            │   └── pulumi_azure_adapter.py
            └── aws/
                ├── bootstrap_builder.py    # KEEP, reused
                └── pool_metadata.py        # KEEP, validation gate

Filesystem (persistent volume mounted at /var/lib/inferia):
└── pulumi-state/
    ├── .pulumi/                   # Pulumi metadata
    └── inferia-aws/
        └── inferia-pool-<uuid>/   # one stack per pool
            ├── Pulumi.yaml
            └── Pulumi.<stack>.yaml

GHCR (image registry):
└── ghcr.io/inferiaai/inferia-worker:0.1.0    (multi-arch, public)
```

### PulumiAWSAdapter — class shape

```python
class PulumiAWSAdapter(PulumiProvisioningBase, ProviderAdapter):
    ADAPTER_TYPE = AdapterType.CLOUD
    CAPABILITIES = ProviderCapabilities(
        supports_gpu=True,
        supports_cluster_mode=True,
        pricing_model=PricingModel.ON_DEMAND,
        features={"cloud": "aws", "bootstrap": "cloud-init", "iac": "pulumi"},
    )

    async def provision_node(self, *, provider_resource_id, pool_id, region=None,
                              use_spot=False, metadata=None,
                              provider_credential_name=None) -> Dict:
        """
        Synchronous prep + async kick-off:
          1. Validate AWSPoolMetadata if metadata supplied.
          2. Resolve credentials via AwsCredentialResolver.
          3. Mint bootstrap_token (DB INSERT).
          4. Render user-data via bootstrap_builder.build_user_data().
          5. asyncio.create_task(self._provision_async(pool_id, ...)).
          6. Return {provider_instance_id: None, provider: "aws",
                     region, lifecycle_state: "provisioning",
                     metadata: {pulumi_stack: "inferia-pool-<uuid>",
                                bootstrap_id: "<uuid>"}}
        """

    async def _provision_async(self, pool_id, user_data, env_vars, …):
        """
        Owns the long-running Pulumi up call.
          stack = pulumi.automation.create_or_select_stack(
              stack_name=f"inferia-pool-{pool_id}",
              project_name="inferia-aws",
              program=build_ec2_program(...),
              opts=LocalWorkspaceOptions(
                  work_dir=f"{settings.pulumi_state_dir}/inferia-aws",
                  env_vars={"PULUMI_BACKEND_URL": f"file://{settings.pulumi_state_dir}",
                            **env_vars},
              ),
          )
          stack.set_config("aws:region", ConfigValue(region))
          up_result = await stack.up_async(on_output=print)
          outputs = up_result.outputs
          # update compute_pools.metadata with outputs[instance_id]
          # leave lifecycle_state = 'provisioning' until worker registers
        """

    async def wait_for_ready(self, *, provider_instance_id, timeout=900,
                              provider_credential_name=None, region=None) -> str:
        """Polls compute_inventory until a worker with the bootstrap
        token's bootstrap_id has registered, or times out and calls
        deprovision_node."""

    async def deprovision_node(self, *, provider_instance_id,
                                provider_credential_name=None) -> None:
        """stack.destroy_async(); then stack.workspace.remove_stack(...)."""

    async def discover_resources(self, *, region="us-east-1") -> list[Dict]:
        """Same shape as the prior boto3 adapter: paginated
        describe_instance_types, normalized to {provider, provider_resource_id,
        gpu_type, gpu_count, gpu_memory_gb, gpu_vendor, vcpu, ram_gb, region,
        pricing_model, price_per_hour}. Uses pulumi_aws.ec2.get_instance_types
        OR raw boto3 — both acceptable since the credential is already
        resolved."""

    async def get_logs(self, *, provider_instance_id,
                        provider_credential_name=None) -> Dict:
        """Returns the EC2 console output (cloud-init log) for the stack's
        instance. Surfaced in the UI when a worker fails to register."""
```

### build_ec2_program — inline Pulumi program

```python
def build_ec2_program(*, pool_meta, account_defaults, user_data, region) -> Callable:
    def program():
        import pulumi
        import pulumi_aws as aws

        subnet_id = pool_meta.get("subnet_id") or account_defaults.subnet_id
        sg_ids = pool_meta.get("security_group_ids") or account_defaults.security_group_ids
        ami_id = pool_meta.get("ami_id") or account_defaults.ami_id or _latest_dlami_ami(region)
        iam_arn = pool_meta.get("iam_instance_profile") or account_defaults.iam_instance_profile
        root_gb = pool_meta.get("root_volume_gb") or account_defaults.root_volume_gb or 100

        instance = aws.ec2.Instance(
            f"inferia-pool-{pool_id}",
            instance_type=provider_resource_id,
            ami=ami_id,
            subnet_id=subnet_id,
            vpc_security_group_ids=sg_ids or None,
            iam_instance_profile=iam_arn,
            user_data=user_data,
            root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
                volume_size=root_gb,
                volume_type="gp3",
            ),
            tags={
                "Name": f"inferia-pool-{pool_id}",
                "InferiaPoolId": str(pool_id),
                "InferiaOrgId": str(org_id),
                "InferiaBootstrapId": str(bootstrap_id),
            },
        )
        pulumi.export("instance_id", instance.id)
        pulumi.export("public_dns", instance.public_dns)
        pulumi.export("private_ip", instance.private_ip)
    return program
```

### AMI lookup helper

`_latest_dlami_ami(region) -> str` resolves the latest AWS Deep Learning
AMI by querying SSM Public Parameters:

```python
async def _latest_dlami_ami(region: str) -> str:
    """Returns the latest AWS DLAMI for Ubuntu 22.04 + NVIDIA driver.
    Cached per region for 1h. boto3-direct (creds already in env)."""
    import boto3
    ssm = boto3.client("ssm", region_name=region)
    resp = ssm.get_parameter(
        Name="/aws/service/deeplearning/ami/x86_64/oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
    )
    return resp["Parameter"]["Value"]
```

Lives in `adapters/pulumi/ami.py`.

### AwsCredentialResolver

```python
class MissingCredentialsError(ValueError):
    """Raised when ProvidersConfig.cloud.aws is missing required fields."""

async def resolve_aws_env(cfg: ProvidersConfig) -> dict[str, str]:
    aws = cfg.cloud.aws
    if not aws.access_key_id or not aws.secret_access_key:
        raise MissingCredentialsError(
            "AWS credentials missing — set them in Settings → Providers → AWS."
        )
    # Belt-and-braces: the gateway's _preserve_masked_secrets guard already
    # rejects round-tripped masks on save, but defend at the adapter
    # boundary too. Import the shared helper.
    from inferia.services.api_gateway.management.configuration import _is_masked
    if _is_masked(aws.access_key_id) or _is_masked(aws.secret_access_key):
        raise MissingCredentialsError("Refusing masked credentials.")
    return {
        "AWS_ACCESS_KEY_ID": aws.access_key_id,
        "AWS_SECRET_ACCESS_KEY": aws.secret_access_key,
        "AWS_DEFAULT_REGION": aws.region or "us-east-1",
    }
```

The credential resolver is the only place credentials are ever decrypted
into plaintext. They flow only to the Pulumi subprocess's environment;
nothing is logged.

### Pool lifecycle states

```
pending          createpool received, validating request
   ↓
provisioning    Pulumi stack.up_async() running OR running but worker hasn't registered yet
   ↓
ready           Pulumi succeeded AND worker has registered (single transition)
   ↓
running         (synonym for ready in the dashboard; same DB state)
   ↓
failed (terminal): up failed, ready-timeout fired, or any unrecoverable error
```

Single transition `provisioning → ready` keeps state simple: the pool is
"not yet usable" while either Pulumi is running OR the worker hasn't
finished its cloud-init/register dance. Both legs run in parallel and
the pool only flips to `ready` when both succeed.

`failed` pools can be deleted (calls `stack.destroy_async()`) or
retried (calls `stack.up_async()` again — Pulumi reconciles).

## Configuration

### Orchestration settings (env / inferia.yaml)

- `INFERIA_PULUMI_STATE_DIR` — default `/var/lib/inferia/pulumi-state`. Host mount required.
- `INFERIA_PULUMI_PASSPHRASE` — default `""` (no Pulumi secret encryption). Only set when stack secrets are needed; not used today.
- `INFERIA_WORKER_IMAGE` — default `ghcr.io/inferiaai/inferia-worker`. Already in place.
- `INFERIA_WORKER_IMAGE_TAG` — default `0.1.0`. Already in place.
- `INFERIA_BOOTSTRAP_TOKEN_TTL_SECONDS` — default `3600`. Already in place.

### Per-pool (`compute_pools.metadata` jsonb)

All optional; account defaults from `ProvidersConfig.cloud.aws` apply when fields are missing:

- `subnet_id`
- `security_group_ids[]`
- `ami_id`
- `iam_instance_profile`
- `root_volume_gb`
- `worker_image_tag`
- Plus internal fields written by the adapter: `pulumi_stack`, `bootstrap_id`, `instance_id`, `error`

## Pulumi state on disk

```
/var/lib/inferia/pulumi-state/
├── .pulumi/
│   ├── meta.yaml
│   ├── credentials.json     # empty for local backend
│   └── locks/
└── inferia-aws/             # project_name
    ├── Pulumi.yaml
    ├── Pulumi.inferia-pool-<uuid-1>.yaml
    ├── Pulumi.inferia-pool-<uuid-2>.yaml
    └── ...
```

Volume mounting (docker-compose):

```yaml
inferia-app:
  volumes:
    - pulumi-state:/var/lib/inferia/pulumi-state
```

Backup is a tar of that directory. Restore is `tar -xC /var/lib/inferia/`.
Stack lock files are recovered by `pulumi cancel <stack>` if a previous
`up` crashed; the adapter exposes a small "force-unlock" admin endpoint
that calls `stack.cancel()` for emergencies.

## Data flow (one provision, end-to-end)

See diagram in section 3 of the brainstorming transcript. Summary:

1. Dashboard → `createpool` (provider=aws).
2. Adapter: validate metadata → resolve credentials → mint bootstrap token →
   render user-data → kick off `_provision_async` via `asyncio.create_task`.
3. createpool returns 200 immediately with `lifecycle_state='provisioning'`.
4. Background task: `LocalWorkspace.create_or_select_stack` → `set_config`
   → `up_async(program=build_ec2_program(...))`.
5. Pulumi creates `aws.ec2.Instance` with tags + user-data.
6. EC2 cloud-init: install Docker → `docker pull ghcr.io/inferiaai/inferia-worker:0.1.0` →
   `docker run --restart=always --gpus=all -e BOOTSTRAP_TOKEN=… ...`.
7. Worker boots, runs `cloudenv.Detect()`, POSTs `/v1/workers/register` with
   `bootstrap_token` + cloud-env metadata.
8. CP consumes the token atomically, mints `WorkerJWT`, flips
   `compute_inventory` to `state='ready'`, returns `{node_id, worker_jwt}`.
9. `wait_for_ready` (polling inventory by bootstrap_id) sees ready;
   pool moves to `running`.
10. Worker opens `/v1/workers/channel` WS with `Authorization: Bearer <WorkerJWT>`.
11. UI poll sees `lifecycle_state='running'`, flips spinner to "Pool ready".

## Failure modes

| Failure | Surface | Recovery |
|---|---|---|
| AWS creds missing | `MissingCredentialsError` raised before any AWS call | 400 to UI with link to Settings → Providers → AWS |
| Invalid AWS metadata (bad subnet/SG/AMI shape) | `AWSPoolMetadata` validation | 422 with the exact Pydantic error |
| Pulumi `up` rejects (quota, AMI, IAM) | `pulumi.automation.errors.CommandError` | `lifecycle_state='failed'`, `metadata.error`; admin can retry |
| Pulumi state lock from a previous crash | `up` raises lock error | "Force unlock" admin endpoint calls `stack.cancel()` |
| EC2 boots but cloud-init fails | `wait_for_ready` polling timeout | `stack.destroy_async()`, pool FAILED, console-output in metadata |
| Bootstrap token expires before worker registers | atomic UPDATE returns 0 rows in worker register | worker keeps retrying; manual re-provision is the path |
| Concurrent `up` for same pool | lifecycle_state guard + Pulumi's own stack lock | second call returns 409 |
| Pulumi state dir unwritable | `LocalWorkspace` constructor raises | 500 surfaced to UI with a clear "configure persistent volume" message |
| Worker WS drops mid-LoadModel | existing dedup + reconnect logic | no spec change |
| Operator terminates EC2 from AWS console | `stack.refresh()` then `destroy()` will reconcile | UI shows pool as unreachable; admin must `pool delete` to clean Pulumi state |

## Testing

See section 4 of the brainstorming transcript. Summary of new test files:

- `adapters/pulumi/test_pulumi_aws_adapter.py` — 12+ tests covering happy
  path, missing creds, metadata validation, up failure rollback, account
  defaults, per-pool overrides, wait_for_ready polling, timeout-terminate,
  deprovision, discover_resources, get_logs, concurrent provision rejection.
- `adapters/pulumi/test_pulumi_gcp_adapter.py` / `test_pulumi_azure_adapter.py` —
  parallel coverage of the same paths.
- `adapters/pulumi/test_credentials.py` — env resolution, missing creds,
  masked-value rejection, per-provider variants.
- `adapters/pulumi/test_base.py` — state dir perms, stack naming,
  workspace constructor args, no-secrets-in-stack-config invariant.
- `adapter_engine/test_registry.py` — extend to assert Pulumi adapters are
  what `get_adapter('aws'/'gcp'/'azure')` returns; `lambda` and `runpod`
  raise `ValueError`.

Coverage target: ≥95% on every new file in `adapters/pulumi/`. Pulumi
automation API mocked end-to-end — no live AWS calls in CI.

## Open / deferred

- ECS / EKS / Fargate Pulumi modes.
- Multi-replica orchestration (requires S3 state backend, lock service).
- Pulumi Cloud as an alternative state backend (one flag flip).
- Lambda Cloud / Runpod adapters (need Pulumi dynamic providers).
- Spot fleets / ASGs / multiple instances per pool (today: one instance per pool).
- Live pricing via AWS Pricing API (static fallback for now).
- Image signing (cosign) for the inferia-worker GHCR image.

## References

- `services/orchestration/services/adapter_engine/base.py` — `ProviderAdapter` contract.
- `services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py` — reused for user-data.
- `services/orchestration/services/adapter_engine/adapters/aws/pool_metadata.py` — reused for validation.
- `services/api_gateway/config.py` — `AWSConfig` already carries account-wide provisioning defaults.
- `inferia-worker` repo — GHCR-published image, IMDS-based runtime detection, bootstrap token register flow.
- Pulumi Automation API: https://www.pulumi.com/docs/iac/packages-and-automation/automation-api/
- `pulumi_aws` reference: https://www.pulumi.com/registry/packages/aws/
