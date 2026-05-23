"""Local Qwen3 smoke orchestrator.

Steps mirror docs/specs/2026-05-23-qwen3-smoke-design.md §5.3. Brings up the
sibling inferia-worker compose after minting a real bootstrap token, then
sequentially deploys Ollama Qwen3 → chats → undeploys → deploys vLLM
Qwen3 → chats → undeploys → tears down.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

from scripts.smoke.lib import APIError, SmokeAPI, SmokeError, SmokeTimeoutError, wait_until


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.worker-local.yml"
UNIFIED_FILE = REPO_ROOT / "deploy" / "docker-compose.unified.yml"

GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@inferia.local")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin")


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


def deploy_and_chat(
    api: SmokeAPI, *, pool_id: str, recipe: str, model_uri: str, config: dict | None,
    ready_timeout: float,
) -> None:
    name = f"smoke-{recipe}-{uuid.uuid4().hex[:6]}"
    dep_id = api.create_deployment(
        pool_id=pool_id, recipe=recipe, model_uri=model_uri, name=name, config=config,
    )
    print(f"  deployment {dep_id} created; waiting for running...")
    try:
        wait_until(
            lambda: api.get_deployment(dep_id) if api.get_deployment(dep_id).get("state") == "running" else None,
            timeout=ready_timeout, interval=4.0,
        )
        out = api.chat(dep_id, "Say hello in one short sentence.")
        if not out.strip():
            raise SmokeError(f"{recipe}: empty chat response")
        print(f"  {recipe} OK — {out!r}")
    finally:
        api.delete_deployment(dep_id)
        time.sleep(8)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep-on-fail", action="store_true")
    p.add_argument("--engines", default="ollama,vllm", help="comma-separated list")
    args = p.parse_args()

    ensure_preconditions()

    api = SmokeAPI(base_url=GATEWAY_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    pool_name = f"smoke-local-{uuid.uuid4().hex[:6]}"
    pool_id = api.create_pool(provider="worker", name=pool_name)
    print(f"pool {pool_id} ({pool_name}) created")

    bootstrap = api.mint_bootstrap_token(pool_id, ttl_hours=1)
    inf_tok = secrets.token_hex(32)

    env = os.environ.copy()
    env.update(
        BOOTSTRAP_TOKEN=bootstrap["token"],
        POOL_ID=pool_id,
        INFERENCE_TOKEN=inf_tok,
    )

    fail = False
    try:
        try:
            api._request(
                "POST", f"/v1/compute-pools/{pool_id}/inference-token",
                json={"token": inf_tok},
            )
        except APIError as e:
            if e.status != 404:
                raise

        run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], env=env)

        print("waiting for worker to register as ready...")
        wait_until(
            lambda: api.list_workers(pool_id)
            if any(w.get("status") == "ready" for w in api.list_workers(pool_id))
            else None,
            timeout=60.0, interval=2.0,
        )
        print("worker ready")

        for engine in args.engines.split(","):
            engine = engine.strip()
            if engine == "ollama":
                deploy_and_chat(
                    api, pool_id=pool_id, recipe="ollama",
                    model_uri="ollama://qwen3:0.6b",
                    config=None, ready_timeout=180.0,
                )
            elif engine == "vllm":
                deploy_and_chat(
                    api, pool_id=pool_id, recipe="vllm",
                    model_uri="hf://Qwen/Qwen3-0.6B",
                    config={
                        "gpu_memory_utilization": 0.5,
                        "max_model_len": 4096,
                        "dtype": "bfloat16",
                    },
                    ready_timeout=300.0,
                )
            else:
                raise SmokeError(f"unknown engine {engine}")
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    if fail and args.keep_on_fail:
        print("--keep-on-fail set; leaving stack up", file=sys.stderr)
        return 1

    print("tearing down worker compose and pool...")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=False,
    )
    api.destroy_pool(pool_id)
    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
