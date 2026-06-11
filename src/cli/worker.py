"""
inferiallm worker — operator CLI for incubating new inferia-worker nodes.

The CLI talks directly to the orchestration service (default
``http://localhost:8080``) using the shared ``INTERNAL_API_KEY``, bypassing
the api_gateway's user-JWT layer. That suits the operator workflow where
the CLI runs on the same host as the orchestration service (or any host
that already knows the internal key); for dashboard-driven flows the same
endpoints are reached through the api_gateway proxy under
``/api/v1/admin/workers/...``.

Subcommands
-----------

* ``worker token --pool-id <uuid>``       — mint a bootstrap token + .env snippet.
* ``worker compose --pool-id <uuid> ...`` — scaffold a complete docker-compose
                                            directory you can ``cd && docker compose up``.
* ``worker list --pool-id <uuid>``        — print the workers in a pool, with
                                            connection state and last heartbeat.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error as urlerror, request as urlrequest


DEFAULT_ORCHESTRATION_URL = "http://localhost:8080"


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib-only so the CLI doesn't drag httpx into the import path
# of a 1-line `inferiallm worker token ...` invocation).
# ---------------------------------------------------------------------------


def _http_request(method: str, url: str, *, headers: dict, body: bytes | None = None) -> tuple[int, bytes]:
    req = urlrequest.Request(url=url, method=method, headers=headers, data=body)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urlerror.HTTPError as e:
        return e.code, e.read()


def _resolve(args, name: str, env: str, default: str | None = None) -> str:
    val = getattr(args, name.replace("-", "_"), None) or os.getenv(env) or default
    if not val:
        sys.exit(
            f"error: --{name} not supplied and {env} not set in environment",
        )
    return val


def _orchestration_base(args) -> str:
    return _resolve(args, "orchestration-url", "ORCHESTRATION_URL", DEFAULT_ORCHESTRATION_URL)


def _internal_headers(args) -> dict:
    key = _resolve(args, "internal-api-key", "INTERNAL_API_KEY")
    return {
        "X-Internal-API-Key": key,
        "X-Gateway-Request": "true",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Subcommands.
# ---------------------------------------------------------------------------


def _cmd_token(args) -> None:
    base = _orchestration_base(args)
    payload = json.dumps({
        "pool_id": args.pool_id,
        "ttl_hours": int(args.ttl_hours),
    }).encode("utf-8")
    status, body = _http_request(
        "POST",
        f"{base}/v1/admin/workers/tokens",
        headers=_internal_headers(args),
        body=payload,
    )
    if status != 200:
        sys.stderr.write(f"mint failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)
    print("# inferia-worker bootstrap token issued")
    print(f"# pool_id        : {data['pool_id']}")
    print(f"# expires_at     : {data['expires_at']}")
    print(f"# control_plane  : {data['control_plane_url']}")
    print()
    print("# --- paste these into your inferia-worker .env ---")
    print(data["env_snippet"])


def _cmd_compose(args) -> None:
    base = _orchestration_base(args)
    payload = json.dumps({
        "pool_id": args.pool_id,
        "ttl_hours": int(args.ttl_hours),
    }).encode("utf-8")
    status, body = _http_request(
        "POST",
        f"{base}/v1/admin/workers/tokens",
        headers=_internal_headers(args),
        body=payload,
    )
    if status != 200:
        sys.stderr.write(f"mint failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)

    # Patch in the operator-supplied NODE_NAME + WORKER_ADVERTISE_URL.
    env_lines = []
    for line in data["env_snippet"].splitlines():
        if line.startswith("NODE_NAME="):
            env_lines.append(f"NODE_NAME={args.node_name}")
        elif line.startswith("WORKER_ADVERTISE_URL="):
            env_lines.append(f"WORKER_ADVERTISE_URL={args.advertise_url}")
        else:
            env_lines.append(line)
    env_lines.append(f"INFERIA_WORKER_IMAGE={args.worker_image}")
    env_lines.append(f"INFERENCE_PORT={int(args.inference_port)}")
    env_blob = "\n".join(env_lines) + "\n"

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".env").write_text(env_blob)
    (out_dir / "docker-compose.yml").write_text(_compose_template())

    print(f"# scaffolded inferia-worker deploy at {out_dir}")
    print(f"#   pool_id       : {data['pool_id']}")
    print(f"#   node_name     : {args.node_name}")
    print(f"#   advertise_url : {args.advertise_url}")
    print()
    print("# next steps:")
    print(f"#   cd {out_dir}")
    print("#   docker compose up -d")
    print("#   docker compose logs -f")


def _cmd_list(args) -> None:
    base = _orchestration_base(args)
    status, body = _http_request(
        "GET",
        f"{base}/v1/admin/workers/pool/{args.pool_id}",
        headers=_internal_headers(args),
    )
    if status != 200:
        sys.stderr.write(f"list failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)
    workers = data.get("workers", [])
    if not workers:
        print(f"# no workers in pool {args.pool_id}")
        return
    fmt = "{:38} {:20} {:6} {:9} {:8} {:30} {}"
    print(fmt.format("NODE_ID", "NODE_NAME", "STATE", "CONNECTED", "CPU%", "ADVERTISE_URL", "LAST_HEARTBEAT"))
    for w in workers:
        print(fmt.format(
            w.get("node_id", "-"),
            (w.get("node_name") or "-")[:20],
            w.get("state", "-"),
            "true" if w.get("connected") else "false",
            (w.get("used", {}) or {}).get("cpu_pct", "-"),
            (w.get("advertise_url") or "-")[:30],
            w.get("last_heartbeat") or "-",
        ))


# ---------------------------------------------------------------------------
# Compose template used by `worker compose`.
# ---------------------------------------------------------------------------


def _compose_template() -> str:
    return (
        "# Generated by `inferiallm worker compose`. Run with:\n"
        "#   docker compose up -d\n"
        "services:\n"
        "  worker:\n"
        "    image: ${INFERIA_WORKER_IMAGE}\n"
        "    container_name: inferia-worker\n"
        "    restart: unless-stopped\n"
        "    environment:\n"
        "      CONTROL_PLANE_URL:        ${CONTROL_PLANE_URL}\n"
        "      BOOTSTRAP_TOKEN:          ${BOOTSTRAP_TOKEN}\n"
        "      NODE_NAME:                ${NODE_NAME}\n"
        "      POOL_ID:                  ${POOL_ID}\n"
        "      WORKER_ADVERTISE_URL:     ${WORKER_ADVERTISE_URL}\n"
        "      INFERENCE_TOKEN:          ${INFERENCE_TOKEN}\n"
        "      WORKER_LISTEN_ADDR:       0.0.0.0:8080\n"
        "      LOG_LEVEL:                info\n"
        "    volumes:\n"
        "      - /var/run/docker.sock:/var/run/docker.sock:rw\n"
        "      - worker-state:/var/lib/inferia-worker\n"
        "    ports:\n"
        "      - \"${INFERENCE_PORT}:8080\"\n"
        "volumes:\n"
        "  worker-state:\n"
        "    driver: local\n"
    )


# ---------------------------------------------------------------------------
# Entry.
# ---------------------------------------------------------------------------


def run_worker_command(args) -> None:
    action = getattr(args, "worker_action", None)
    if action == "token":
        _cmd_token(args)
    elif action == "compose":
        _cmd_compose(args)
    elif action == "list":
        _cmd_list(args)
    else:
        sys.exit(f"error: unknown worker action: {action}")
