"""``inferiallm models`` — inspect the model cache and import into it.

The import path exists for installs with no internet access, where the usual
``source: hf`` download can never succeed. An operator stages the model files
in the cache volume's ``import/`` directory and names the entry here.

Stdlib-only HTTP, matching cli/worker.py, so a one-line invocation does not
drag httpx into the import path.
"""
from __future__ import annotations

import json
import os
import sys
from urllib import error as urlerror, request as urlrequest

from common.service_ports import orchestration_http_url

DEFAULT_ORCHESTRATION_URL = orchestration_http_url()


def _http_request(
    method: str, url: str, *, headers: dict, body: bytes | None = None,
) -> tuple[int, bytes]:
    req = urlrequest.Request(url=url, method=method, headers=headers, data=body)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urlerror.HTTPError as e:
        return e.code, e.read()


def _resolve(args, name: str, env: str, default: str | None = None) -> str:
    val = getattr(args, name.replace("-", "_"), None) or os.getenv(env) or default
    if not val:
        sys.exit(f"error: --{name} not supplied and {env} not set in environment")
    return val


def _base(args) -> str:
    return _resolve(
        args, "orchestration-url", "ORCHESTRATION_URL", DEFAULT_ORCHESTRATION_URL
    )


def _headers(args) -> dict:
    return {
        "X-Internal-API-Key": _resolve(args, "internal-api-key", "INTERNAL_API_KEY"),
        "X-Gateway-Request": "true",
        "Content-Type": "application/json",
    }


def _fail(action: str, status: int, body: bytes) -> None:
    sys.stderr.write(
        f"{action} failed (status={status}):\n{body.decode('utf-8', 'replace')}\n"
    )
    sys.exit(1)


def _cmd_list(args) -> None:
    status, body = _http_request(
        "GET", f"{_base(args)}/v1/models", headers=_headers(args)
    )
    if status != 200:
        _fail("list", status, body)
    models = json.loads(body).get("models", [])
    if not models:
        print("no models cached")
        return
    print(f"{'SOURCE':<8} {'MODEL':<40} {'REVISION':<12} {'STATUS':<12} PINNED")
    for m in models:
        print(
            f"{m.get('source', ''):<8} {m.get('model_id', ''):<40} "
            f"{m.get('revision', ''):<12} {m.get('status', ''):<12} "
            f"{'yes' if m.get('pinned') else 'no'}"
        )


def _cmd_import(args) -> None:
    # `main` is a HuggingFace branch; an Ollama model has no such tag, and
    # deploys look for `latest`.
    revision = args.revision or ("latest" if args.format == "ollama" else "main")
    payload = json.dumps({
        "format": args.format,
        "from": args.from_,
        "model_id": args.model_id,
        "revision": revision,
        "engine": args.engine,
    }).encode("utf-8")
    status, body = _http_request(
        "POST", f"{_base(args)}/v1/models/import",
        headers=_headers(args), body=payload,
    )
    if status != 202:
        _fail("import", status, body)
    print(f"importing {args.model_id} ({args.format}) from staging entry {args.from_!r}")
    print("track it with: inferiallm models list")


def run_models_command(args) -> None:
    {"list": _cmd_list, "import": _cmd_import}[args.models_action](args)
