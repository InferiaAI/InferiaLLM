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
* ``node pool aws-config POOL_ID --subnet=... --security-group=... ...``
                                                           → set AWS metadata on pool
* ``node pool show POOL_ID``                               → show pool details

Endpoint notes
--------------
Pool operations hit the orchestration deployment router (prefix ``/deployment``):
  GET  /deployment/pool/{pool_id}           – basic pool info (no metadata field)
  PATCH /deployment/updatepool/{pool_id}    – merge metadata; ``metadata=null`` is
                                              a safe no-op read (returns current row)
"""

from __future__ import annotations

import json
import os
import re
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
# Pool subcommands (U4/U5)
# ---------------------------------------------------------------------------

POOL_SUBNET_RE = re.compile(r"^subnet-[0-9a-f]{8,17}$")
POOL_SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
POOL_AMI_RE = re.compile(r"^ami-[0-9a-f]{8,17}$")
POOL_IAM_RE = re.compile(r"^arn:aws:iam::\d{12}:instance-profile/.+$")


def cmd_pool_aws_config(args) -> None:
    """inferiallm node pool aws-config POOL_ID --subnet=... --security-group=... ...

    Validates AWS-specific metadata client-side, then reads the pool to
    confirm it exists and has provider=aws, then PATCHes the metadata.
    The backend merges the new fields with any existing metadata so no
    previously-set keys are clobbered.

    Endpoints used:
        GET  /deployment/pool/{pool_id}        (existence + provider check)
        PATCH /deployment/updatepool/{pool_id} (metadata merge-write)
    """
    # --- Client-side validation ---
    if not args.subnet:
        print("error: --subnet is required", file=sys.stderr)
        raise SystemExit(2)
    if not POOL_SUBNET_RE.match(args.subnet):
        print(f"error: invalid subnet_id {args.subnet!r}", file=sys.stderr)
        raise SystemExit(2)
    if not args.security_group:
        print("error: at least one --security-group is required", file=sys.stderr)
        raise SystemExit(2)
    for sg in args.security_group:
        if not POOL_SG_RE.match(sg):
            print(f"error: invalid security_group_id {sg!r}", file=sys.stderr)
            raise SystemExit(2)
    if args.ami and not POOL_AMI_RE.match(args.ami):
        print(f"error: invalid ami_id {args.ami!r}", file=sys.stderr)
        raise SystemExit(2)
    if args.iam_profile and not POOL_IAM_RE.match(args.iam_profile):
        print(f"error: invalid iam_instance_profile {args.iam_profile!r}", file=sys.stderr)
        raise SystemExit(2)
    if args.root_gb is not None and not (10 <= args.root_gb <= 16384):
        print("error: --root-gb must be 10..16384", file=sys.stderr)
        raise SystemExit(2)
    if args.image_tag and (not args.image_tag.strip() or any(c.isspace() for c in args.image_tag)):
        print("error: --image-tag must be non-empty and contain no whitespace", file=sys.stderr)
        raise SystemExit(2)

    base = _orchestration_base(args)
    headers = _internal_headers(args, org_id=getattr(args, "org_id", None))

    # Verify the pool exists and has provider=aws before writing.
    status, body = _http("GET", f"{base}/deployment/pool/{args.pool_id}", headers=headers)
    if status == 404:
        print(f"error: pool not found: {args.pool_id}", file=sys.stderr)
        raise SystemExit(1)
    if status != 200:
        print(
            f"error: GET pool {args.pool_id} returned {status}: "
            f"{body.decode(errors='replace')}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    pool = json.loads(body)

    if pool.get("provider") != "aws":
        print(
            f"error: pool provider is {pool.get('provider')!r}, not aws",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Build the metadata dict to merge.  The backend (updatepool) reads the
    # existing row and merges before writing, so we only need to send the
    # keys we want to set/update.
    metadata: dict = {}
    metadata["subnet_id"] = args.subnet
    metadata["security_group_ids"] = list(args.security_group)
    if args.ami:
        metadata["ami_id"] = args.ami
    if args.iam_profile:
        metadata["iam_instance_profile"] = args.iam_profile
    if args.root_gb is not None:
        metadata["root_volume_gb"] = args.root_gb
    if args.image_tag:
        metadata["worker_image_tag"] = args.image_tag

    patch_body = json.dumps({"metadata": metadata}).encode()
    status, body = _http(
        "PATCH",
        f"{base}/deployment/updatepool/{args.pool_id}",
        headers={**headers, "Content-Type": "application/json"},
        body=patch_body,
    )
    if status not in (200, 204):
        print(
            f"error: PATCH returned {status}: {body.decode(errors='replace')}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"updated pool {args.pool_id} with AWS metadata")


def cmd_pool_show(args) -> None:
    """inferiallm node pool show POOL_ID

    Prints pool details with provider-aware metadata formatting.

    Endpoints used:
        GET   /deployment/pool/{pool_id}        (basic fields)
        PATCH /deployment/updatepool/{pool_id}  (metadata read — metadata=null
              is a documented no-op that returns the current metadata row)
    """
    base = _orchestration_base(args)
    headers = _internal_headers(args, org_id=getattr(args, "org_id", None))

    # Fetch basic pool info (pool_id, pool_name, provider, lifecycle_state, …).
    status, body = _http("GET", f"{base}/deployment/pool/{args.pool_id}", headers=headers)
    if status == 404:
        print(f"error: pool not found: {args.pool_id}", file=sys.stderr)
        raise SystemExit(1)
    if status != 200:
        print(f"error: GET pool returned {status}", file=sys.stderr)
        raise SystemExit(1)
    pool = json.loads(body)

    # Fetch metadata via PATCH with metadata=null (safe no-op read).
    status2, body2 = _http(
        "PATCH",
        f"{base}/deployment/updatepool/{args.pool_id}",
        headers={**headers, "Content-Type": "application/json"},
        body=json.dumps({"metadata": None}).encode(),
    )
    metadata: dict = {}
    if status2 in (200, 204) and body2:
        try:
            patch_resp = json.loads(body2)
            raw = patch_resp.get("metadata") or {}
            if isinstance(raw, str):
                raw = json.loads(raw)
            metadata = raw or {}
        except (json.JSONDecodeError, ValueError):
            metadata = {}

    # pool_id / pool_name are the field names from GET /deployment/pool/{id}
    print(f"ID:        {pool.get('pool_id', pool.get('id', '-'))}")
    print(f"Provider:  {pool.get('provider', '-')}")
    print(f"Name:      {pool.get('pool_name', pool.get('name', '-'))}")
    print(f"State:     {pool.get('lifecycle_state', pool.get('state', '-'))}")

    if pool.get("provider") == "aws":
        print("\nAWS configuration:")
        print(f"  subnet_id:            {metadata.get('subnet_id', '-')}")
        sgs = metadata.get("security_group_ids", [])
        print(f"  security_group_ids:   {', '.join(sgs) if sgs else '-'}")
        print(f"  ami_id:               {metadata.get('ami_id', '(auto)')}")
        print(f"  iam_instance_profile: {metadata.get('iam_instance_profile', '-')}")
        print(f"  root_volume_gb:       {metadata.get('root_volume_gb', 130)}")
        print(f"  worker_image_tag:     {metadata.get('worker_image_tag', '(default)')}")
    elif metadata:
        print("\nMetadata:")
        print(json.dumps(metadata, indent=2))


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
    elif action == "pool":
        sub = args.pool_action
        if sub == "aws-config":
            cmd_pool_aws_config(args)
        elif sub == "show":
            cmd_pool_show(args)
        else:
            sys.exit(f"error: unknown pool action: {sub}")
    else:
        sys.exit(f"error: unknown node action: {action}")
