"""AWS Qwen3 smoke orchestrator with defense-in-depth teardown.

Layers (per spec §7.3):
  1. Pre-flight reject of pre-existing smoke-aws-* nodes.
  2. try/finally + atexit delete.
  3. Cost printout + 5s Ctrl-C window.
  4. Wall-clock guard via outer Makefile timeout(1).
  5. boto3 post-teardown verification.
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
import uuid

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
    SmokeError,
    SmokeTimeoutError,
    _decode_jwt_claims,
    cost_estimate,
    wait_until,
)


GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
INFERENCE_URL = os.environ.get("SMOKE_INFERENCE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "change-me-immediately")
WORKER_REPO = os.environ.get("SMOKE_WORKER_REPO", "inferia/inferia-worker")


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def trigger_ghcr_build(tag_suffix: str) -> None:
    """Dispatch the docker-publish workflow and block until success."""
    run([
        "gh", "workflow", "run", "docker-publish.yml",
        "-R", WORKER_REPO,
        "-f", f"tag_suffix={tag_suffix}",
    ])
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["gh", "run", "list", "-R", WORKER_REPO,
             "--workflow", "docker-publish.yml", "--limit", "1",
             "--json", "status,conclusion"],
            capture_output=True, text=True, check=True,
        )
        if '"status":"completed"' in out.stdout:
            if '"conclusion":"success"' in out.stdout:
                return
            sys.exit(f"GHCR build failed: {out.stdout}")
        time.sleep(20)
    sys.exit("GHCR build timed out (20 min)")


def preflight(api: SmokeAPI) -> dict:
    """Verify AWS provider is configured and no stale smoke nodes exist."""
    try:
        providers = api._request("GET", "/management/config/providers").json()
    except APIError as e:
        sys.exit(f"unable to list providers: {e}")
    if isinstance(providers, dict):
        provider_list = providers.get("providers", [])
    else:
        provider_list = providers
    aws = next(
        (p for p in provider_list
         if p.get("provider_type") == "aws" and p.get("configured")),
        None,
    )
    if not aws:
        sys.exit("AWS provider not configured. Configure it in Settings → Providers first.")
    try:
        nodes_resp = api._request("GET", "/api/v1/nodes/").json()
    except APIError as e:
        sys.exit(f"unable to list nodes: {e}")
    nodes = nodes_resp.get("nodes", nodes_resp) if isinstance(nodes_resp, dict) else nodes_resp
    stale = [n for n in (nodes or [])
             if str(n.get("node_name", "")).startswith("smoke-aws-")]
    if stale:
        names = ", ".join(n.get("node_name", "?") for n in stale)
        sys.exit(f"pre-existing smoke node(s) found: {names}. Delete them first.")
    return aws


def verify_no_running_instances(pool_id: str) -> None:
    """boto3 describe-instances; fail if anything is running/pending for our pool."""
    try:
        import boto3
    except ImportError:
        print("(boto3 not installed; skipping post-teardown verification)", file=sys.stderr)
        return
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    ec2 = boto3.client("ec2", region_name=region)
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:InferiaPoolId", "Values": [pool_id]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping"]},
        ],
    )
    leftover = [
        i["InstanceId"]
        for r in resp.get("Reservations", [])
        for i in r.get("Instances", [])
    ]
    if leftover:
        sys.exit(f"INSTANCES STILL LIVE after teardown: {leftover}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--instance-type", default="g4dn.xlarge")
    p.add_argument("--region", default=None)
    p.add_argument("--worker-image-tag", default=None,
                   help="if set, skip GHCR build and reuse this tag")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-on-fail", action="store_true")
    args = p.parse_args()

    api = SmokeAPI(base_url=GATEWAY_URL, inference_url=INFERENCE_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    aws = preflight(api)
    if args.dry_run:
        print("dry-run OK: AWS provider configured, no stale nodes")
        return 0

    print(cost_estimate(args.instance_type, hours=1/6))
    if not os.environ.get("SMOKE_NO_CONFIRM"):
        print("Ctrl-C within 5s to abort...", flush=True)
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            sys.exit("aborted")

    tag = args.worker_image_tag or f"smoke-{int(time.time())}"
    if not args.worker_image_tag:
        trigger_ghcr_build(tag)

    node_id: str | None = None
    pool_id: str | None = None

    def teardown() -> None:
        if node_id is None:
            return
        try:
            api.delete_node(node_id)
        except Exception as e:
            print(f"teardown delete_node failed: {e}", file=sys.stderr)
        if pool_id:
            try:
                verify_no_running_instances(pool_id)
            except SystemExit as e:
                print(str(e), file=sys.stderr)

    atexit.register(teardown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: sys.exit("SIGINT"))

    fail = False
    try:
        ts = int(time.time())
        node_name = f"smoke-aws-{ts}-{uuid.uuid4().hex[:4]}"
        added = api.add_provider_node(
            provider="aws",
            node_name=node_name,
            spec={
                "instance_type": args.instance_type,
                "region": args.region or aws.get("default_region"),
                "worker_image_tag": tag,
                **(aws.get("metadata") or {}),
            },
            credential_name=aws.get("credential_name"),
        )
        node_id = added.get("node_id")
        bootstrap = added.get("bootstrap_token")
        if bootstrap:
            pool_id = _decode_jwt_claims(bootstrap).get("pool_id")
        if not node_id or not pool_id:
            raise SmokeError(f"add_provider_node missing node_id/pool_id: {added}")
        print(f"node {node_id} (pool {pool_id}) provisioning; waiting for worker...")
        wait_until(
            lambda: api.list_workers(pool_id)
            if any(w.get("state") == "ready" for w in api.list_workers(pool_id))
            else None,
            timeout=600.0, interval=10.0,
        )
        from scripts.smoke.local import deploy_and_chat
        deploy_and_chat(api, pool_id=pool_id, engine="ollama", gpu_per_replica=1)
        deploy_and_chat(api, pool_id=pool_id, engine="vllm", gpu_per_replica=1)
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
