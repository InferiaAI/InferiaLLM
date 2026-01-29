import argparse
import sys
import os
import subprocess
import multiprocessing
from inferia.startup_ui import StartupUI
from dotenv import load_dotenv, find_dotenv
from inferia.inferiadocs import (
    show_inferia,
    show_filtration_docs,
    show_inference_docs,
    show_orchestration_docs,
)


KNOWN_COMMANDS = {
    "init",
    "start",
}


def _load_env():
    """
    Load environment variables for local/dev usage.
    In Docker / K8s, env vars are injected externally.
    """
    # Use find_dotenv to locate .env in parent directories if not in CWD
    load_dotenv(find_dotenv(), override=False)


def run_filtration_gateway(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("Filtration Gateway API"))
        from inferia.gateways.filtration_gateway.main import start_api

        start_api()
        if queue:
            queue.put(
                ServiceStarted(
                    "Filtration Gateway API", detail="Listening on port 8000"
                )
            )
    except Exception as e:
        if queue:
            queue.put(ServiceFailed("Filtration Gateway API", error=str(e)))
        else:
            print(f"Error starting Filtration Gateway: {e}")


def run_inference_gateway(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    try:
        if queue:
            queue.put(ServiceStarting("Inference Gateway API"))
        from inferia.gateways.inference_gateway.main import start_api

        start_api()
        if queue:
            queue.put(
                ServiceStarted("Inference Gateway API", detail="Listening on port 8001")
            )
    except Exception as e:
        if queue:
            queue.put(ServiceFailed("Inference Gateway API", error=str(e)))
        else:
            print(f"Error starting Inference Gateway: {e}")


def run_orchestration_gateway(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    # Helper to inject paths mimicking orchestrator.sh
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(base_dir, "services/orchestration/app")
    gateway_path = os.path.join(base_dir, "gateways/orchestration_gateway")

    sys.path.insert(0, app_path)
    sys.path.insert(0, gateway_path)

    try:
        if queue:
            queue.put(ServiceStarting("Orchestration Gateway API"))
        from inferia.gateways.orchestration_gateway.main import start_api

        start_api()
        if queue:
            queue.put(
                ServiceStarted(
                    "Orchestration Gateway API", detail="Listening on port 8080"
                )
            )
    except Exception as e:
        if queue:
            queue.put(ServiceFailed("Orchestration Gateway API", error=str(e)))
        else:
            print(f"Error starting Orchestration Gateway: {e}")


def run_worker(queue=None):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    base_dir = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(base_dir, "services/orchestration/app")
    sys.path.insert(0, app_path)

    try:
        if queue:
            queue.put(ServiceStarting("Orchestration Worker"))
        import asyncio
        from inferia.services.orchestration.app.services.model_deployment.worker_main import (
            main,
        )

        asyncio.run(main())
        if queue:
            queue.put(
                ServiceStarted(
                    "Orchestration Worker", detail="Connected to message broker"
                )
            )
    except Exception as e:
        if queue:
            queue.put(ServiceFailed("Orchestration Worker", error=str(e)))
        else:
            print(f"Error starting Orchestration Worker: {e}")


def run_nosana_sidecar(queue=None):
    """
    Runs the DePIN Sidecar (Node.js service).
    """
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed

    base_dir = os.path.dirname(os.path.abspath(__file__))
    sidecar_dir = os.path.join(
        base_dir, "services/orchestration/app/services/depin-sidecar"
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
            print("[DePIN] Installing dependencies...")
            subprocess.run(["npm", "install"], cwd=sidecar_dir, check=True)

        env = os.environ.copy()
        if not env.get("FILTRATION_URL"):
            env["FILTRATION_URL"] = "http://localhost:8000"

        print("[DePIN] Launching sidecar...")
        subprocess.Popen(
            ["npx", "tsx", "src/server.ts"],
            cwd=sidecar_dir,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=env,
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

            def translate_path(self, path):
                resolved = super().translate_path(path)
                if os.path.exists(resolved):
                    return resolved
                _, ext = os.path.splitext(path)
                if ext and ext.lower() not in [".html", ".htm"]:
                    return resolved
                return os.path.join(os.getcwd(), "index.html")

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


def run_init():
    from inferia.cli_init import init_databases

    init_databases()


def run_orchestration_stack():
    """
    Runs the full orchestration stack: API, Worker, and DePIN Sidecar.
    """
    queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=run_orchestration_gateway, name="orchestration-api", args=(queue,)
        ),
        multiprocessing.Process(
            target=run_worker, name="orchestration-worker", args=(queue,)
        ),
        multiprocessing.Process(
            target=run_nosana_sidecar, name="nosana-sidecar", args=(queue,)
        ),
    ]

    print("[CLI] Starting Orchestration Stack (API, Worker, DePIN Sidecar)...")
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


def run_all():
    # Run all services efficiently by spawning them as direct children
    queue = multiprocessing.Queue()
    ui = StartupUI(queue, total=6)

    processes = [
        # Orchestration Stack
        multiprocessing.Process(
            target=run_orchestration_gateway,
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
            args=(queue,),
        ),
        # Inference & Filtration
        multiprocessing.Process(
            target=run_inference_gateway,
            name="inference",
            args=(queue,),
        ),
        multiprocessing.Process(
            target=run_filtration_gateway,
            name="filtration",
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
        add_help=False,
    )
    parser.add_argument("command", nargs="?", help="InferiaLLM command")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize Inferia databases")

    # --- New START Command ---
    start_parser = sub.add_parser("start", help="Start Inferia services")
    start_parser.add_argument(
        "service",
        nargs="?",
        default="all",
        choices=["all", "filtration", "inference", "orchestration"],
        help="Service to start (default: all)",
    )

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

            if service == "all":
                if wants_help(flags):
                    show_inferia()
                else:
                    run_all()

            elif service == "filtration":
                if wants_help(flags):
                    show_filtration_docs()
                else:
                    run_filtration_gateway()

            elif service == "inference":
                if wants_help(flags):
                    show_inference_docs()
                else:
                    run_inference_gateway()

            elif service == "orchestration":
                if wants_help(flags):
                    show_orchestration_docs()
                else:
                    run_orchestration_stack()

        elif cmd == "init":
            run_init()

        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nShutting down Inferia...")
        sys.exit(0)


if __name__ == "__main__":
    main()
