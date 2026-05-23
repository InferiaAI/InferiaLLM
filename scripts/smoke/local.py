"""Local Qwen3 smoke orchestrator (node-centric API, post-2026-05-14 refactor).

Flow:
  1. Login to gateway as superadmin.
  2. POST /api/v1/nodes/add/worker to create a worker node and receive
     bootstrap_token, control_plane_url, inference_token, env_snippet.
     pool_id is extracted from the bootstrap_token JWT claim.
  3. Bring up the sibling inferia-worker compose with those creds; wait
     for the worker to register as 'ready' against the gateway.
  4. For each engine in --engines (default ollama,vllm), deploy Qwen3
     via the orchestration proxy, wait for ready, chat once, delete.
  5. Tear down the worker compose; delete the node (releasing the pool).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
    SmokeError,
    SmokeTimeoutError,
    WorkerNode,
    wait_until,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.worker-local.yml"

GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
INFERENCE_URL = os.environ.get("SMOKE_INFERENCE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "change-me-immediately")


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def ensure_preconditions() -> None:
    img = subprocess.run(
        ["docker", "image", "inspect", "inferia-worker:smoke"],
        capture_output=True,
    )
    if img.returncode != 0:
        sys.exit("inferia-worker:smoke image not found. Run `make smoke-local-up` first.")
    existing = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name=^inferia-worker$"],
        capture_output=True, text=True,
    )
    if existing.stdout.strip():
        sys.exit("A container named 'inferia-worker' already exists. Remove it and retry.")
    # Model containers are spawned by the worker via docker.sock as sibling
    # containers on the host — `docker compose down -v` on the worker compose
    # does not touch them, so a previous failed run can leave them holding
    # host ports (notably 19000+). Wipe any stragglers before starting.
    leftovers = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=^inferia-(ollama|vllm|infinity|tei|tgi)-"],
        capture_output=True, text=True,
    )
    ids = [x for x in leftovers.stdout.split() if x]
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], check=False, capture_output=True)


_ENGINE_PRESETS: dict[str, dict[str, object]] = {
    "ollama": {
        "model_name": "qwen3",
        "model_version": "0.6b",
        "engine": "ollama",
        "configuration": None,
        "ready_timeout": 900.0,
        "chat_model": "qwen3:0.6b",
    },
    "vllm": {
        "model_name": "Qwen/Qwen3-0.6B",
        "model_version": "latest",
        "engine": "vllm",
        "configuration": {
            "gpu_memory_utilization": 0.5,
            "max_model_len": 4096,
            "dtype": "bfloat16",
        },
        "ready_timeout": 900.0,
        "chat_model": "Qwen/Qwen3-0.6B",
    },
}


def deploy_and_chat(
    api: SmokeAPI,
    *,
    pool_id: str,
    engine: str,
    gpu_per_replica: int = 0,
) -> None:
    preset = _ENGINE_PRESETS.get(engine)
    if preset is None:
        raise SmokeError(f"unknown engine {engine!r}")

    resp = api.deploy_model(
        pool_id=pool_id,
        model_name=preset["model_name"],
        model_version=preset["model_version"],
        engine=preset["engine"],
        replicas=1,
        gpu_per_replica=gpu_per_replica,
        configuration=preset["configuration"],
    )
    dep_id = resp.get("deployment_id") or resp.get("id")
    if not dep_id:
        raise SmokeError(f"{engine}: deploy_model returned no id: {resp}")
    print(f"  deployment {dep_id} created; waiting for ready...")
    try:
        def _ready():
            s = api.get_deployment_status(dep_id)
            state = (s.get("state") or s.get("status") or "").upper()
            if state in {"RUNNING", "READY", "SUCCEEDED"}:
                return s
            if state in {"FAILED", "ERROR", "TERMINATED"}:
                raise SmokeError(f"{engine}: deployment {dep_id} entered terminal state {state}: {s.get('error_message')}")
            return None
        wait_until(_ready, timeout=preset["ready_timeout"], interval=4.0)
        out = api.chat(preset["chat_model"], "Say hello in one short sentence.")
        if not out.strip():
            raise SmokeError(f"{engine}: empty chat response")
        print(f"  {engine} OK — {out!r}")
    finally:
        api.delete_deployment(dep_id)
        time.sleep(8)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep-on-fail", action="store_true")
    p.add_argument("--engines", default="ollama,vllm", help="comma-separated list")
    p.add_argument("--gpu-per-replica", type=int, default=0,
                   help="0 = CPU smoke; 1+ requires GPU on the worker host")
    args = p.parse_args()

    ensure_preconditions()

    api = SmokeAPI(base_url=GATEWAY_URL, inference_url=INFERENCE_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    node_name = f"smoke-local-{uuid.uuid4().hex[:6]}"
    node = api.add_worker_node(
        node_name=node_name,
        advertise_url="http://inferia-worker:8080",
    )
    print(f"node {node.node_id} (pool {node.pool_id}) created")

    env = os.environ.copy()
    env.update(
        BOOTSTRAP_TOKEN=node.bootstrap_token,
        POOL_ID=node.pool_id,
        INFERENCE_TOKEN=node.inference_token,
        NODE_NAME=node_name,
        CONTROL_PLANE_URL=node.control_plane_url,
    )

    fail = False
    try:
        run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], env=env)

        print("waiting for worker to register as ready...")
        wait_until(
            lambda: api.list_workers(node.pool_id)
            if any(w.get("state") == "ready" for w in api.list_workers(node.pool_id))
            else None,
            timeout=90.0, interval=2.0,
        )
        print("worker ready")

        for engine in args.engines.split(","):
            engine = engine.strip()
            if not engine:
                continue
            deploy_and_chat(
                api, pool_id=node.pool_id, engine=engine,
                gpu_per_replica=args.gpu_per_replica,
            )
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    if fail and args.keep_on_fail:
        print("--keep-on-fail set; leaving stack up", file=sys.stderr)
        return 1

    print("tearing down worker compose and node...")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=False, env=env,
    )
    api.delete_node(node.node_id)
    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
