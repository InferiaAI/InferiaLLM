import argparse
import sys
import os
import shutil
import subprocess
import multiprocessing
import traceback
from inferia.startup_ui import StartupUI
from dotenv import load_dotenv, find_dotenv
from inferia.inferiadocs import (
    show_inferia,
    show_api_gateway_docs,
    show_inference_docs,
    show_orchestration_docs,
)


KNOWN_COMMANDS = {
    "init",
    "start",
    "migrate",
    "write-dashboard-config",
    "providers",
    "worker",
    "node",
}


def _load_env():
    """
    Load environment variables for local/dev usage.
    In Docker / K8s, env vars are injected externally.
    """
    # Use find_dotenv to locate .env in parent directories if not in CWD
    load_dotenv(find_dotenv(), override=False)


def run_api_gateway_service(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("API Gateway Service"))
        from inferia.services.api_gateway.main import start_api

        if queue:
            queue.put(
                ServiceStarted("API Gateway Service", detail="Listening on port 8000")
            )
        start_api()
    except Exception as e:
        print(f"[FATAL] API Gateway Service failed to start: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if queue:
            queue.put(ServiceFailed("API Gateway Service", error=str(e)))


def run_inference_service(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("Inference Service"))
        from inferia.services.inference.main import start_api

        if queue:
            queue.put(
                ServiceStarted("Inference Service", detail="Listening on port 8001")
            )
        start_api()
    except Exception as e:
        print(f"[FATAL] Inference Service failed to start: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if queue:
            queue.put(ServiceFailed("Inference Service", error=str(e)))


def run_orchestration_service(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("Orchestration Service"))
        from inferia.services.orchestration.main import start_api

        if queue:
            queue.put(
                ServiceStarted("Orchestration Service", detail="Listening on port 8080")
            )
        start_api()
    except Exception as e:
        print(f"[FATAL] Orchestration Service failed to start: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if queue:
            queue.put(ServiceFailed("Orchestration Service", error=str(e)))


def run_worker(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("Orchestration Worker"))

        # When the orchestration HTTP server runs the deployment dispatcher
        # in-process (the default — see server.py), this standalone worker
        # process would compete for the same Redis stream messages and
        # additionally cannot reach the in-process WorkerRegistry. So we
        # short-circuit to an idle loop here.
        if os.environ.get("INFERIA_INPROC_DEPLOYMENT_WORKER", "1") != "0":
            import time

            if queue:
                queue.put(
                    ServiceStarted(
                        "Orchestration Worker",
                        detail="Idle (dispatcher co-located in HTTP server)",
                    )
                )
            while True:
                time.sleep(3600)
            return

        import asyncio
        from inferia.services.orchestration.services.model_deployment.worker_main import (
            main,
        )

        if queue:
            queue.put(
                ServiceStarted(
                    "Orchestration Worker", detail="Connected to message broker"
                )
            )
        asyncio.run(main())
    except Exception as e:
        print(f"[FATAL] Orchestration Worker failed to start: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        if queue:
            queue.put(ServiceFailed("Orchestration Worker", error=str(e)))


def run_nosana_sidecar(queue=None, env: str = "production"):
    """
    Runs the DePIN Sidecar (Node.js service).
    """
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    base_dir = os.path.dirname(os.path.abspath(__file__))
    sidecar_dir = os.path.join(
        base_dir, "services/orchestration/services/depin-sidecar"
    )

    print(f"[DePIN] Starting Sidecar from {sidecar_dir}")

    if not os.path.isdir(sidecar_dir):
        print(f"[DePIN] Error: Sidecar directory not found at {sidecar_dir}")
        return

    node_modules = os.path.join(sidecar_dir, "node_modules")

    try:
        if queue:
            queue.put(ServiceStarting("nosana-sidecar"))
        if not os.path.isdir(node_modules):
            if env == "dev":
                print("[DePIN] Installing dependencies...")
                subprocess.run(["npm", "install"], cwd=sidecar_dir, check=True)
            else:
                print("[DePIN] Installing production dependencies...")
                subprocess.run(["npm", "install", "--omit=dev"], cwd=sidecar_dir, check=True)

        node_env = os.environ.copy()
        if not node_env.get("API_GATEWAY_URL"):
            node_env["API_GATEWAY_URL"] = "http://localhost:8000"

        print("[DePIN] Launching sidecar...")
        cmd = ["npm", "run", "dev"] if env == "dev" else ["npm", "start"]

        subprocess.Popen(
            cmd,
            cwd=sidecar_dir,
            env=node_env,
        )
        if queue:
            queue.put(ServiceStarted("nosana-sidecar", "Node.js"))

    except FileNotFoundError as e:
        print("[Nosana] Error: 'npx' command not found. Ensure Node.js is installed.")
        if queue:
            queue.put(ServiceFailed("nosana-sidecar", str(e)))
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as e:
        print("[DePIN] Sidecar process failed")
        if queue:
            queue.put(ServiceFailed("nosana-sidecar", str(e)))
    except Exception as e:
        print(f"[Nosana] Error: {e}")
        if queue:
            queue.put(ServiceFailed("nosana-sidecar", str(e)))


def run_skypilot_server(queue=None):
    """
    Starts the SkyPilot API server (required for cloud provider orchestration).
    Only starts if skypilot is installed.
    """
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("SkyPilot API Server"))

        # Check if skypilot is installed
        import importlib.util
        if importlib.util.find_spec("sky") is None:
            msg = "SkyPilot not installed. Skipping. Install with: pip install 'skypilot[gcp]'"
            print(f"[SkyPilot] {msg}")
            if queue:
                queue.put(ServiceFailed("SkyPilot API Server", error=msg))
            return

        # Check if already running
        import sky
        try:
            sky.status()
            # If this succeeds, server is already running
            print("[SkyPilot] API server already running.")
            if queue:
                queue.put(ServiceStarted("SkyPilot API Server", detail="Already running"))
            return
        except Exception:
            pass

        # Start the server
        print("[SkyPilot] Starting API server...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "sky.api.cli", "start"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait briefly for it to start
        import time
        time.sleep(5)

        if proc.poll() is not None:
            # Process exited, try alternative command
            proc = subprocess.Popen(
                ["sky", "api", "start"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(5)

        if proc.poll() is None or proc.returncode == 0:
            print("[SkyPilot] API server started successfully.")
            if queue:
                queue.put(ServiceStarted("SkyPilot API Server", detail="Running"))
        else:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            if "already running" in stderr.lower():
                print("[SkyPilot] API server already running.")
                if queue:
                    queue.put(ServiceStarted("SkyPilot API Server", detail="Already running"))
            else:
                print(f"[SkyPilot] Failed to start: {stderr}")
                if queue:
                    queue.put(ServiceFailed("SkyPilot API Server", error=stderr[:200]))

    except Exception as e:
        print(f"[SkyPilot] Error: {e}")
        if queue:
            queue.put(ServiceFailed("SkyPilot API Server", error=str(e)))


def run_dashboard(queue=None):
    """
    Runs the Dashboard on a separate HTTP server (port 3001).
    """
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    import http.server
    import socketserver

    base_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_dir = os.path.join(base_dir, "dashboard")
    port = 3001

    if not os.path.isdir(dashboard_dir):
        print(f"[Dashboard] Error: Dashboard directory not found at {dashboard_dir}")
        if queue:
            queue.put(
                ServiceFailed("Dashboard", f"Directory not found: {dashboard_dir}")
            )
        return

    try:
        if queue:
            queue.put(ServiceStarting("Dashboard"))
        os.chdir(dashboard_dir)

        class SPAHandler(http.server.SimpleHTTPRequestHandler):
            extensions_map = {
                **http.server.SimpleHTTPRequestHandler.extensions_map,
                ".js": "application/javascript",
                ".mjs": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".svg": "image/svg+xml",
                ".woff": "font/woff",
                ".woff2": "font/woff2",
            }

            def do_GET(self):
                """Override to handle SPA routing."""
                # Get the physical path for the request
                path = self.translate_path(self.path)
                
                # If path doesn't exist, check if we should serve index.html
                if not os.path.exists(path):
                    # Only fallback if it's not a request for a static asset (no extension or .html)
                    _, ext = os.path.splitext(self.path)
                    if not ext or ext.lower() in [".html", ".htm"]:
                        self.path = "/index.html"
                
                return super().do_GET()

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("", port), SPAHandler) as httpd:
            print(f"[Dashboard] Serving at http://localhost:{port}/")
            if queue:
                queue.put(ServiceStarted("Dashboard", f"http://localhost:{port}/"))
            httpd.serve_forever()

    except OSError as e:
        if "Address already in use" in str(e):
            print(f"[Dashboard] Port {port} already in use")
            if queue:
                queue.put(ServiceFailed("Dashboard", f"Port {port} already in use"))
        else:
            if queue:
                queue.put(ServiceFailed("Dashboard", str(e)))
    except Exception as e:
        print(f"[Dashboard] Error: {e}")
        if queue:
            queue.put(ServiceFailed("Dashboard", str(e)))

def build_dashboard():
    """
    Builds the Dashboard.
    If package/src/inferia/dashboard doesn't exist, runs npm install and
    npm run build in apps/dashboard, then copies the dist output to
    package/src/inferia/dashboard.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_dest = os.path.join(base_dir, "dashboard")

    if os.path.isdir(dashboard_dest):
        shutil.rmtree(dashboard_dest)
        print(f"[inferia:init] Info: Dashboard directory already exists at {dashboard_dest}, rebuilding.")

    # Locate the apps/dashboard source directory
    # base_dir is <repo>/package/src/inferia, walk up to repo root
    repo_root = os.path.abspath(os.path.join(base_dir, "..", "..", ".."))
    dashboard_src = os.path.join(repo_root, "apps", "dashboard")

    if not os.path.isdir(dashboard_src):
        print(f"[inferia:init] Error: Dashboard source directory not found at {dashboard_src}")
        return

    print(f"[inferia:init] Building Dashboard from {dashboard_src}")

    try:
        print("[inferia:init] Installing dashboard dependencies...")
        subprocess.run(["npm", "install"], cwd=dashboard_src, check=True)

        print("[inferia:init] Building dashboard...")
        subprocess.run(["npm", "run", "build"], cwd=dashboard_src, check=True)

        dist_dir = os.path.join(dashboard_src, "dist")
        if not os.path.isdir(dist_dir):
            print(f"[inferia:init] Error: Build output not found at {dist_dir}")
            return

        shutil.copytree(dist_dir, dashboard_dest)
        print(f"[inferia:init] Dashboard built and copied to {dashboard_dest}")

    except FileNotFoundError:
        print(
            "[inferia:init] Error: 'npm' command not found. Ensure Node.js is installed."
        )
    except subprocess.CalledProcessError as e:
        print(f"[inferia:init] Dashboard build failed: {e}")
    except Exception as e:
        print(f"[inferia:init] Error building dashboard: {e}")

def build_sidecar():
    """
    Builds the DePIN Sidecar (Node.js service).
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sidecar_dir = os.path.join(
        base_dir, "services/orchestration/services/depin-sidecar"
    )

    print(f"[inferia:init] Building Sidecar at {sidecar_dir}")

    if not os.path.isdir(sidecar_dir):
        print(f"[inferia:init] Error: Sidecar directory not found at {sidecar_dir}")
        return

    node_modules = os.path.join(sidecar_dir, "node_modules")

    try:
        if not os.path.isdir(node_modules):
            print("[inferia:init] Installing sidecar dependencies...")
            subprocess.run(["npm", "install"], cwd=sidecar_dir, check=True)

        print("[inferia:init] Building sidecar...")
        subprocess.run(["npm", "run", "build"], cwd=sidecar_dir, check=True)
        print("[inferia:init] Sidecar built successfully.")

    except FileNotFoundError:
        print(
            "[inferia:init] Error: 'npm' command not found. Ensure Node.js is installed."
        )
    except subprocess.CalledProcessError as e:
        print(f"[inferia:init] Sidecar build failed: {e}")
    except Exception as e:
        print(f"[inferia:init] Error building sidecar: {e}")


def run_write_dashboard_config(config_path: str | None = None, dashboard_dir: str | None = None) -> None:
    """Write window.__RUNTIME_CONFIG__ = {...}; to the installed dashboard's config.js.

    Dashboard URLs are env-only: DASHBOARD_API_GATEWAY_URL, DASHBOARD_INFERENCE_URL,
    DASHBOARD_WEB_SOCKET_URL, DASHBOARD_SIDECAR_URL. If a var is unset or empty,
    the field is written as an empty string (matching legacy entrypoint.sh behaviour).

    The --config / config_path argument is accepted for forward-compat with existing
    scripts but is not used — yaml no longer carries dashboard URLs.

    The function is intentionally import-light and DB-free so it can be called
    from entrypoint.sh before the database is available.
    """
    import json
    import inferia

    # Resolve the dashboard directory.
    if dashboard_dir is None:
        base = inferia.__path__[0]
        dashboard_dir = os.path.join(base, "dashboard")

    if not os.path.isdir(dashboard_dir):
        # No bundled dashboard (e.g. headless / server-only installs). No-op.
        return

    config = {
        "API_GATEWAY_URL": os.environ.get("DASHBOARD_API_GATEWAY_URL", "") or "",
        "INFERENCE_URL": os.environ.get("DASHBOARD_INFERENCE_URL", "") or "",
        "WEB_SOCKET_URL": os.environ.get("DASHBOARD_WEB_SOCKET_URL", "") or "",
        "SIDECAR_URL": os.environ.get("DASHBOARD_SIDECAR_URL", "") or "",
        # Auth mode is runtime-configurable (not baked into the SPA build): the
        # dashboard reads AUTH_PROVIDER from this runtime config so a single image
        # serves local / oidc / inferiaauth. Falls back to VITE_AUTH_PROVIDER, then
        # AUTH_PROVIDER. Empty => the SPA's built-in default ("local").
        "AUTH_PROVIDER": (
            os.environ.get("VITE_AUTH_PROVIDER")
            or os.environ.get("AUTH_PROVIDER", "")
            or ""
        ),
    }

    config_js_path = os.path.join(dashboard_dir, "config.js")
    with open(config_js_path, "w", encoding="utf-8") as f:
        f.write("window.__RUNTIME_CONFIG__ = " + json.dumps(config) + ";")

    print(
        f"[inferiallm write-dashboard-config] wrote {config_js_path} "
        f"(api_gateway_url={config['API_GATEWAY_URL']!r}, "
        f"inference_url={config['INFERENCE_URL']!r}, "
        f"web_socket_url={config['WEB_SOCKET_URL']!r}, "
        f"sidecar_url={config['SIDECAR_URL']!r})"
    )


def run_migrate():
    """Apply pending database migrations without full init."""
    import asyncio
    from inferia.cli_init import run_migrations

    asyncio.run(run_migrations())


def run_init(env: str = "production"):
    from inferia.cli_init import init_databases

    init_databases()
    if env == "dev":
        build_dashboard()
        build_sidecar()
    else:
        print("[inferia:init] Running in production mode: skipping builds.")


def run_orchestration_stack(env: str = "production"):
    """
    Runs the full orchestration stack: API, Worker, and DePIN Sidecar.
    """
    queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=run_orchestration_service, name="orchestration-api", args=(queue,)
        ),
        multiprocessing.Process(
            target=run_worker, name="orchestration-worker", args=(queue,)
        ),
        multiprocessing.Process(
            target=run_nosana_sidecar, name="nosana-sidecar", args=(queue, env)
        ),
        multiprocessing.Process(
            target=run_skypilot_server, name="skypilot-api", args=(queue,)
        ),
    ]

    print("[CLI] Starting Orchestration Stack (API, Worker, DePIN Sidecar, SkyPilot)...")
    for p in processes:
        p.start()

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n[CLI] Shutting down Orchestration Stack...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join()


def run_all(env: str = "production"):
    # Run all services efficiently by spawning them as direct children
    queue = multiprocessing.Queue()
    ui = StartupUI(queue, total=7)

    processes = [
        # Core Gateway
        multiprocessing.Process(
            target=run_api_gateway_service,
            name="api-gateway",
            args=(queue,),
        ),
        # Microservices
        multiprocessing.Process(
            target=run_inference_service,
            name="inference",
            args=(queue,),
        ),
        # Orchestration Stack
        multiprocessing.Process(
            target=run_orchestration_service,
            name="orchestration-api",
            args=(queue,),
        ),
        multiprocessing.Process(
            target=run_worker,
            name="orchestration-worker",
            args=(queue,),
        ),
        multiprocessing.Process(
            target=run_nosana_sidecar,
            name="nosana-sidecar",
            args=(queue, env),
        ),
        multiprocessing.Process(
            target=run_skypilot_server,
            name="skypilot-api",
            args=(queue,),
        ),
        # Dashboard
        multiprocessing.Process(
            target=run_dashboard,
            name="dashboard",
            args=(queue,),
        ),
    ]

    print("[CLI] Starting All Services...")
    for p in processes:
        p.start()

    ui.run()  # Blocking call to run the UI

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n[CLI] Shutting down Inferia...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join()


def wants_help(flags: set[str]) -> bool:
    return any(f.startswith(("-h", "--help", "help")) for f in flags)


def main(argv=None):
    _load_env()

    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("help", "--help", "-h"):
        show_inferia()
        return

    parser = argparse.ArgumentParser(
        prog="inferiallm",
        description="InferiaLLM CLI – distributed inference & orchestration platform",
    )
    sub = parser.add_subparsers(
        dest="command", required=True, help="Available commands"
    )

    init_parser = sub.add_parser("init", help="Initialize Inferia databases")
    init_parser.add_argument(
        "--env",
        choices=["dev", "production"],
        default="production",
        help="Environment to initialize for (default: production)",
    )

    sub.add_parser("migrate", help="Apply pending database migrations")

    # --- New START Command ---
    start_parser = sub.add_parser("start", help="Start Inferia services")
    start_parser.add_argument(
        "service",
        nargs="?",
        default="all",
        choices=[
            "all",
            "api-gateway",
            "inference",
            "orchestration",
            "skypilot",
        ],
        help="Service to start (default: all)",
    )
    start_parser.add_argument(
        "--env",
        choices=["dev", "production"],
        default="production",
        help="Environment to run in (default: production)",
    )
    start_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to unified YAML config file (default: auto-discover via "
             "INFERIA_CONFIG, ./inferia.yaml, or /etc/inferia/inferia.yaml)",
    )

    # --- write-dashboard-config ---
    wdc_parser = sub.add_parser(
        "write-dashboard-config",
        help="Write dashboard runtime config.js from DASHBOARD_* env vars",
    )
    wdc_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Accepted for forward-compat but unused; dashboard URLs are env-only",
    )
    wdc_parser.add_argument(
        "--dashboard-dir",
        type=str,
        default=None,
        dest="dashboard_dir",
        help="Override the installed dashboard directory (useful for tests)",
    )

    # --- providers sub-command ---
    providers_parser = sub.add_parser(
        "providers",
        help="Manage provider credentials (DB-backed)",
    )
    providers_sub = providers_parser.add_subparsers(
        dest="providers_action", required=True, help="Action"
    )

    # list
    list_p = providers_sub.add_parser("list", help="List provider credentials")
    list_p.add_argument(
        "--provider",
        choices=["aws", "gcp", "azure", "ibm", "nosana"],
        default=None,
        help="Filter by provider",
    )

    # add
    add_p = providers_sub.add_parser("add", help="Add a provider credential")
    add_p.add_argument("provider", choices=["aws", "gcp", "azure", "ibm", "nosana"])
    add_p.add_argument("--name", required=True, help="Credential name (e.g. prod, default)")
    add_p.add_argument("--type", required=True, dest="type", help="Credential type (e.g. api_key, access_key_id)")
    add_p.add_argument("--value", required=True, help="Credential value (plaintext)")
    add_p.add_argument("--active", default="true", help="Is active (true/false, default: true)")

    # remove
    rm_p = providers_sub.add_parser("remove", help="Remove a provider credential")
    rm_p.add_argument("provider", choices=["aws", "gcp", "azure", "ibm", "nosana"])
    rm_p.add_argument("--name", required=True, help="Credential name to remove")

    # update
    upd_p = providers_sub.add_parser("update", help="Update an existing provider credential")
    upd_p.add_argument("provider", choices=["aws", "gcp", "azure", "ibm", "nosana"])
    upd_p.add_argument("--name", required=True, help="Credential name to update")
    upd_p.add_argument("--type", default=None, dest="type", help="Credential type (required for cloud providers)")
    upd_p.add_argument("--value", default=None, help="New value")
    upd_p.add_argument("--active", default=None, help="Set active state (true/false)")

    # --- worker sub-command -------------------------------------------------
    worker_parser = sub.add_parser(
        "worker",
        help="Incubate an inferia-worker node (mint bootstrap token, scaffold compose)",
    )
    worker_sub = worker_parser.add_subparsers(
        dest="worker_action", required=True, help="Action",
    )

    # worker token — mint and print
    tok_p = worker_sub.add_parser(
        "token",
        help="Mint a bootstrap token for a pool and print a .env snippet",
    )
    tok_p.add_argument("--pool-id", required=True, help="UUID of the target compute pool")
    tok_p.add_argument(
        "--ttl-hours", type=int, default=1,
        help="Token lifetime in hours (1-24, default 1)",
    )
    tok_p.add_argument(
        "--orchestration-url",
        default=None,
        help="Orchestration service URL (default: http://localhost:8080)",
    )
    tok_p.add_argument(
        "--internal-api-key",
        default=None,
        help="Internal API key (default: INTERNAL_API_KEY env var)",
    )

    # worker compose — scaffold a ready-to-run docker-compose directory
    cmp_p = worker_sub.add_parser(
        "compose",
        help="Scaffold an inferia-worker compose dir for a fresh GPU host",
    )
    cmp_p.add_argument("--pool-id", required=True, help="UUID of the target compute pool")
    cmp_p.add_argument(
        "--node-name", required=True,
        help="Stable node name unique within the pool",
    )
    cmp_p.add_argument(
        "--advertise-url", required=True,
        help="URL the control plane will use to reach this worker's inference port",
    )
    cmp_p.add_argument(
        "--out-dir", default="./inferia-worker-deploy",
        help="Directory to write .env + docker-compose.yml into",
    )
    cmp_p.add_argument(
        "--ttl-hours", type=int, default=1,
        help="Bootstrap token lifetime in hours (1-24, default 1)",
    )
    cmp_p.add_argument(
        "--worker-image",
        default="ghcr.io/inferiaai/inferia-worker:0.1.0",
        help="Worker container image (full repository:tag). Published via the "
             "InferiaAI/inferia-worker GitHub Action on v* tags. Note that "
             "docker/metadata-action strips the leading 'v' from semver tags, "
             "so git tag v0.1.0 produces GHCR tag 0.1.0.",
    )
    cmp_p.add_argument(
        "--inference-port", type=int, default=8080,
        help="Host port to publish the worker's inference endpoint on",
    )
    cmp_p.add_argument(
        "--orchestration-url",
        default=None,
        help="Orchestration service URL (default: http://localhost:8080)",
    )
    cmp_p.add_argument(
        "--internal-api-key",
        default=None,
        help="Internal API key (default: INTERNAL_API_KEY env var)",
    )

    # worker list — show workers in a pool
    list_w = worker_sub.add_parser(
        "list",
        help="List inferia-worker rows in a pool",
    )
    list_w.add_argument("--pool-id", required=True, help="UUID of the target compute pool")
    list_w.add_argument(
        "--orchestration-url",
        default=None,
        help="Orchestration service URL (default: http://localhost:8080)",
    )
    list_w.add_argument(
        "--internal-api-key",
        default=None,
        help="Internal API key (default: INTERNAL_API_KEY env var)",
    )

    # --- node sub-command (node-centric API) -------------------------------
    node_parser = sub.add_parser(
        "node",
        help="Manage compute nodes (add, list, label, remove)",
    )
    node_sub = node_parser.add_subparsers(
        dest="node_action", required=True, help="Action",
    )

    # node add ...
    node_add = node_sub.add_parser("add", help="Add a node to a pool")
    node_add_sub = node_add.add_subparsers(
        dest="node_add_provider", required=True, help="Provider",
    )

    def _add_common_flags(p):
        p.add_argument("--name", required=True, help="Node name")
        p.add_argument(
            "--label",
            action="append",
            default=[],
            help="Label key=value (repeat for multiple)",
        )
        p.add_argument("--org-id", default=None, help="Organization id (or INFERIA_ORG_ID env)")
        p.add_argument("--orchestration-url", default=None,
                       help="Orchestration URL (default: http://localhost:8080)")
        p.add_argument("--internal-api-key", default=None,
                       help="Internal API key (default: INTERNAL_API_KEY env)")

    nw = node_add_sub.add_parser("worker", help="Add a self-hosted inferia-worker node")
    _add_common_flags(nw)
    nw.add_argument("--advertise-url", default=None,
                    help="URL the control plane should use to reach this worker (operator can fill later)")

    nn = node_add_sub.add_parser("nosana", help="Add a Nosana node")
    _add_common_flags(nn)
    nn.add_argument("--gpu-type", required=True)
    nn.add_argument("--market-address", required=True)
    nn.add_argument("--credential-name", default="default")

    na = node_add_sub.add_parser("akash", help="Add an Akash node")
    _add_common_flags(na)
    na.add_argument("--gpu-type", required=True)
    na.add_argument("--credential-name", default="default")

    # node list
    nl = node_sub.add_parser("list", help="List nodes (optionally filter by labels)")
    nl.add_argument("--label", action="append", default=[], help="Filter by label key=value (AND)")
    nl.add_argument("--org-id", default=None)
    nl.add_argument("--orchestration-url", default=None)
    nl.add_argument("--internal-api-key", default=None)

    # node labels {set,del,get}
    nl_labels = node_sub.add_parser("labels", help="Manage node labels")
    nl_lab_sub = nl_labels.add_subparsers(
        dest="labels_action", required=True, help="Action",
    )

    nl_set = nl_lab_sub.add_parser("set", help="Upsert labels on a node")
    nl_set.add_argument("node_id")
    nl_set.add_argument("kv", nargs="+", help="key=value pairs")
    nl_set.add_argument("--orchestration-url", default=None)
    nl_set.add_argument("--internal-api-key", default=None)

    nl_del = nl_lab_sub.add_parser("del", help="Remove labels from a node")
    nl_del.add_argument("node_id")
    nl_del.add_argument("keys", nargs="+", help="label keys to remove")
    nl_del.add_argument("--orchestration-url", default=None)
    nl_del.add_argument("--internal-api-key", default=None)

    nl_get = nl_lab_sub.add_parser("get", help="Print a node's labels as JSON")
    nl_get.add_argument("node_id")
    nl_get.add_argument("--orchestration-url", default=None)
    nl_get.add_argument("--internal-api-key", default=None)

    # node rm
    n_rm = node_sub.add_parser("rm", help="Soft-delete a node")
    n_rm.add_argument("node_id")
    n_rm.add_argument("--orchestration-url", default=None)
    n_rm.add_argument("--internal-api-key", default=None)

    # node pool …
    np = node_sub.add_parser("pool", help="Pool management")
    np_sub = np.add_subparsers(dest="pool_action", required=True, help="Pool action")

    # node pool aws-config POOL_ID …
    np_cfg = np_sub.add_parser("aws-config", help="Configure AWS metadata on a pool")
    np_cfg.add_argument("pool_id", help="Pool ID (UUID)")
    np_cfg.add_argument("--subnet", required=True, help="VPC subnet ID (subnet-…)")
    np_cfg.add_argument(
        "--security-group",
        action="append",
        default=[],
        dest="security_group",
        help="Security group ID (sg-…) — pass multiple times",
    )
    np_cfg.add_argument("--ami", default=None, help="AMI ID (ami-…) — defaults to auto-detect DLAMI")
    np_cfg.add_argument("--iam-profile", default=None, dest="iam_profile",
                        help="IAM instance profile ARN")
    np_cfg.add_argument("--root-gb", type=int, default=None, dest="root_gb",
                        help="Root EBS volume in GB (10..16384, default 100)")
    np_cfg.add_argument("--image-tag", default=None, dest="image_tag",
                        help="inferia-worker Docker image tag (default 'latest')")
    np_cfg.add_argument("--orchestration-url", default=None)
    np_cfg.add_argument("--internal-api-key", default=None)
    np_cfg.add_argument("--org-id", default=None)

    # node pool show POOL_ID
    np_show = np_sub.add_parser("show", help="Show pool details")
    np_show.add_argument("pool_id", help="Pool ID (UUID)")
    np_show.add_argument("--orchestration-url", default=None)
    np_show.add_argument("--internal-api-key", default=None)
    np_show.add_argument("--org-id", default=None)

    args, unknown = parser.parse_known_args(argv)

    cmd = args.command
    flags = set(unknown)

    if cmd not in KNOWN_COMMANDS:
        print(f"Unknown command: {cmd}")
        print("Use 'inferiallm --help' to see available commands.")
        sys.exit(1)

    try:
        # --- Handle NEW Command Structure ---
        if cmd == "start":
            service = getattr(args, "service", "all")
            env = getattr(args, "env", "production")

            config_path = getattr(args, "config", None)
            if config_path is not None:
                os.environ["INFERIA_CONFIG"] = config_path

            if service == "all":
                if wants_help(flags):
                    show_inferia()
                else:
                    run_all(env=env)

            elif service == "api-gateway":
                if wants_help(flags):
                    show_api_gateway_docs()
                else:
                    run_api_gateway_service()

            elif service == "inference":
                if wants_help(flags):
                    show_inference_docs()
                else:
                    run_inference_service()

            elif service == "orchestration":
                if wants_help(flags):
                    show_orchestration_docs()
                else:
                    run_orchestration_stack(env=env)

            elif service == "skypilot":
                run_skypilot_server()

        elif cmd == "init":
            env = getattr(args, "env", "production")
            run_init(env=env)

        elif cmd == "migrate":
            run_migrate()

        elif cmd == "write-dashboard-config":
            config_path = getattr(args, "config", None)
            dashboard_dir = getattr(args, "dashboard_dir", None)
            if config_path is not None:
                os.environ["INFERIA_CONFIG"] = config_path
            run_write_dashboard_config(
                config_path=config_path,
                dashboard_dir=dashboard_dir,
            )

        elif cmd == "providers":
            from inferia.cli_providers import run_providers_command
            run_providers_command(args)

        elif cmd == "worker":
            from inferia.cli_worker import run_worker_command
            run_worker_command(args)

        elif cmd == "node":
            from inferia.cli_node import run_node_command
            run_node_command(args)

        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nShutting down Inferia...")
        sys.exit(0)


if __name__ == "__main__":
    main()
