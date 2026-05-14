"""
inferiallm node — operator CLI for the node-centric surface.

The CLI hits orchestration directly using the shared ``INTERNAL_API_KEY``.
It mirrors the web flow (POST /v1/nodes/add/{provider}, GET /v1/nodes,
PATCH /v1/nodes/{id}/labels, DELETE /v1/nodes/{id}).

Subcommands
-----------
* ``node add worker --name NAME [--label k=v ...]``        → mints token + .env snippet
* ``node add nosana --gpu-type T --market M [--label ...]`` → submits one Nosana job
* ``node add akash  --gpu-type T [--label ...]``           → submits one Akash deployment
* ``node list [--label k=v ...] [--org-id ORG]``           → table view
* ``node labels set <id> k=v ...``                          → upsert labels
* ``node labels del <id> KEY ...``                          → unset labels
* ``node labels get <id>``                                  → JSON
* ``node rm <id>``                                          → soft delete
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable
from urllib import error as urlerror, request as urlrequest


DEFAULT_ORCHESTRATION_URL = "http://localhost:8080"


def _http(method: str, url: str, *, headers: dict, body: bytes | None = None) -> tuple[int, bytes]:
    req = urlrequest.Request(url=url, method=method, headers=headers, data=body)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urlerror.HTTPError as e:
        return e.code, e.read()


def _orchestration_base(args) -> str:
    val = getattr(args, "orchestration_url", None) or os.getenv(
        "ORCHESTRATION_URL", DEFAULT_ORCHESTRATION_URL
    )
    return val


def _internal_headers(args, org_id: str | None = None) -> dict:
    key = getattr(args, "internal_api_key", None) or os.getenv("INTERNAL_API_KEY")
    if not key:
        sys.exit("error: --internal-api-key not supplied and INTERNAL_API_KEY not set")
    headers = {
        "X-Internal-API-Key": key,
        "X-Gateway-Request": "true",
        "Content-Type": "application/json",
    }
    org = org_id or getattr(args, "org_id", None) or os.getenv("INFERIA_ORG_ID")
    if org:
        headers["X-Organization-ID"] = org
    return headers


def _parse_labels(items: Iterable[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            sys.exit(f"error: label must be key=value: {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Subcommands.
# ---------------------------------------------------------------------------


def _cmd_add_worker(args) -> None:
    if not args.org_id and not os.getenv("INFERIA_ORG_ID"):
        sys.exit("error: --org-id required (or set INFERIA_ORG_ID)")
    base = _orchestration_base(args)
    payload = json.dumps({
        "node_name": args.name,
        "advertise_url": args.advertise_url or "",
        "labels": _parse_labels(args.label),
    }).encode("utf-8")
    status, body = _http(
        "POST", f"{base}/v1/nodes/add/worker",
        headers=_internal_headers(args), body=payload,
    )
    if status != 200:
        sys.stderr.write(f"add failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)
    print(f"# node_id        : {data['node_id']}")
    print(f"# bootstrap_token: {data['bootstrap_token']}")
    print(f"# expires_at     : {data['expires_at']}")
    print()
    print("# --- worker .env (paste into the GPU host) ---")
    print(data["env_snippet"])


def _cmd_add_provider(args, provider: str) -> None:
    if not args.org_id and not os.getenv("INFERIA_ORG_ID"):
        sys.exit("error: --org-id required (or set INFERIA_ORG_ID)")
    base = _orchestration_base(args)
    spec: dict = {}
    if getattr(args, "gpu_type", None):
        spec["gpu_type"] = args.gpu_type
    if getattr(args, "market_address", None):
        spec["market_address"] = args.market_address
    if getattr(args, "credential_name", None):
        spec["credential_name"] = args.credential_name
    payload = json.dumps({
        "node_name": args.name,
        "labels": _parse_labels(args.label),
        "spec": spec,
        "credential_name": getattr(args, "credential_name", None),
    }).encode("utf-8")
    status, body = _http(
        "POST", f"{base}/v1/nodes/add/{provider}",
        headers=_internal_headers(args), body=payload,
    )
    if status != 200:
        sys.stderr.write(f"add failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)
    print(json.dumps(data, indent=2))


def _cmd_list(args) -> None:
    if not args.org_id and not os.getenv("INFERIA_ORG_ID"):
        sys.exit("error: --org-id required (or set INFERIA_ORG_ID)")
    base = _orchestration_base(args)
    qs = ""
    if args.label:
        kvs = []
        for item in args.label:
            if "=" not in item:
                sys.exit(f"error: label must be key=value: {item!r}")
            kvs.append(item)
        from urllib.parse import quote
        qs = "?labels=" + quote(",".join(kvs), safe="=,:")
    status, body = _http(
        "GET", f"{base}/v1/nodes/{qs}",
        headers=_internal_headers(args),
    )
    if status != 200:
        sys.stderr.write(f"list failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    data = json.loads(body)
    rows = data.get("nodes", [])
    if not rows:
        print("# no nodes")
        return
    fmt = "{:38} {:20} {:8} {:14} {:8} {}"
    print(fmt.format("NODE_ID", "NAME", "PROVIDER", "STATE", "GPU", "LABELS"))
    for n in rows:
        print(fmt.format(
            n["id"],
            (n.get("node_name") or "-")[:20],
            (n.get("provider") or "-")[:8],
            (n.get("state") or "-")[:14],
            f"{n.get('gpu_allocated') or 0}/{n.get('gpu_total') or 0}",
            ",".join(f"{k}={v}" for k, v in (n.get("labels") or {}).items()),
        ))


def _cmd_labels_set(args) -> None:
    base = _orchestration_base(args)
    add = _parse_labels(args.kv)
    payload = json.dumps({"add": add, "remove": []}).encode("utf-8")
    status, body = _http(
        "PATCH", f"{base}/v1/nodes/{args.node_id}/labels",
        headers=_internal_headers(args), body=payload,
    )
    if status != 200:
        sys.stderr.write(f"labels set failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    print(json.dumps(json.loads(body).get("labels", {}), indent=2))


def _cmd_labels_del(args) -> None:
    base = _orchestration_base(args)
    payload = json.dumps({"add": {}, "remove": list(args.keys)}).encode("utf-8")
    status, body = _http(
        "PATCH", f"{base}/v1/nodes/{args.node_id}/labels",
        headers=_internal_headers(args), body=payload,
    )
    if status != 200:
        sys.stderr.write(f"labels del failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    print(json.dumps(json.loads(body).get("labels", {}), indent=2))


def _cmd_labels_get(args) -> None:
    base = _orchestration_base(args)
    status, body = _http(
        "GET", f"{base}/v1/nodes/{args.node_id}",
        headers=_internal_headers(args),
    )
    if status != 200:
        sys.stderr.write(f"get failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    print(json.dumps(json.loads(body).get("labels", {}), indent=2))


def _cmd_rm(args) -> None:
    base = _orchestration_base(args)
    status, body = _http(
        "DELETE", f"{base}/v1/nodes/{args.node_id}",
        headers=_internal_headers(args),
    )
    if status != 204:
        sys.stderr.write(f"rm failed (status={status}):\n{body.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    print(f"# node {args.node_id} marked terminated")


# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------


def run_node_command(args) -> None:
    action = getattr(args, "node_action", None)
    if action == "add":
        provider = args.node_add_provider
        if provider == "worker":
            _cmd_add_worker(args)
        elif provider in ("nosana", "akash"):
            _cmd_add_provider(args, provider)
        else:
            sys.exit(f"error: unknown add provider: {provider}")
    elif action == "list":
        _cmd_list(args)
    elif action == "labels":
        sub = args.labels_action
        if sub == "set":
            _cmd_labels_set(args)
        elif sub == "del":
            _cmd_labels_del(args)
        elif sub == "get":
            _cmd_labels_get(args)
        else:
            sys.exit(f"error: unknown labels action: {sub}")
    elif action == "rm":
        _cmd_rm(args)
    else:
        sys.exit(f"error: unknown node action: {action}")
