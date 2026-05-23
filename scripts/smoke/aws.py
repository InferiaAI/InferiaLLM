"""AWS Qwen3 smoke orchestrator with defense-in-depth teardown.

Layers (per spec §7.3):
  1. Pre-flight reject of pre-existing smoke-aws-* pools.
  2. try/finally + atexit destroy.
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
    cost_estimate,
    wait_until,
)


GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@inferia.local")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin")
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
    """Verify AWS provider is configured and no stale smoke pools exist."""
    try:
        providers = api._request("GET", "/v1/providers").json().get("providers", [])
    except APIError as e:
        sys.exit(f"unable to list providers: {e}")
    aws = next((p for p in providers if p.get("provider_type") == "aws" and p.get("configured")), None)
    if not aws:
        sys.exit("AWS provider not configured. Configure it in Settings → Providers first.")
    try:
        pools = api._request("GET", "/v1/compute-pools").json().get("pools", [])
    except APIError as e:
        sys.exit(f"unable to list pools: {e}")
    stale = [p for p in pools if str(p.get("name", "")).startswith("smoke-aws-")]
    if stale:
        names = ", ".join(p["name"] for p in stale)
        sys.exit(f"pre-existing smoke pool(s) found: {names}. Destroy them first.")
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

    api = SmokeAPI(base_url=GATEWAY_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    aws = preflight(api)
    if args.dry_run:
        print("dry-run OK: AWS provider configured, no stale pools")
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

    pool_id: str | None = None

    def teardown() -> None:
        if pool_id is None:
            return
        try:
            api.destroy_pool(pool_id)
        except Exception as e:
            print(f"teardown destroy_pool failed: {e}", file=sys.stderr)
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
        pool_id = api.create_pool(
            provider="aws",
            name=f"smoke-aws-{ts}-{uuid.uuid4().hex[:4]}",
            instance_type=args.instance_type,
            region=args.region or aws.get("default_region"),
            metadata={
                **(aws.get("metadata") or {}),
                "worker_image_tag": tag,
            },
        )
        print(f"pool {pool_id} created; waiting for pulumi succeeded...")
        wait_until(
            lambda: api._request("GET", f"/v1/compute-pools/{pool_id}").json()
                    if api._request("GET", f"/v1/compute-pools/{pool_id}").json().get("pulumi_state") == "succeeded"
                    else None,
            timeout=300.0, interval=10.0,
        )
        print("pulumi succeeded; waiting for worker register...")
        wait_until(
            lambda: api.list_workers(pool_id)
                    if any(w.get("status") == "ready" for w in api.list_workers(pool_id))
                    else None,
            timeout=180.0, interval=5.0,
        )
        from scripts.smoke.local import deploy_and_chat
        deploy_and_chat(
            api, pool_id=pool_id, recipe="ollama",
            model_uri="ollama://qwen3:0.6b", config=None, ready_timeout=240.0,
        )
        deploy_and_chat(
            api, pool_id=pool_id, recipe="vllm",
            model_uri="hf://Qwen/Qwen3-0.6B",
            config={"gpu_memory_utilization": 0.85, "max_model_len": 4096, "dtype": "bfloat16"},
            ready_timeout=360.0,
        )
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
