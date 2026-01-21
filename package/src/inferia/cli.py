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
    "filtration-gateway",
    "inference-gateway",
    "orchestration-gateway",
    "api-start",
}


def _load_env():
    """
    Load environment variables for local/dev usage.
    In Docker / K8s, env vars are injected externally.
    """
    # Use find_dotenv to locate .env in parent directories if not in CWD
    load_dotenv(find_dotenv(), override=False)


def run_filtration_gateway(queue):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    try:
        queue.put(ServiceStarting("Filtration Gateway API"))
        from inferia.gateways.filtration_gateway.main import start_api
        start_api()
        queue.put(ServiceStarted("Filtration Gateway API", detail="Listening on port 8000"))
    except Exception as e:
        queue.put(ServiceFailed("Filtration Gateway API", error=str(e)))


def run_inference_gateway(queue):
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    try:
        queue.put(ServiceStarting("Inference Gateway API"))
        from inferia.gateways.inference_gateway.main import start_api
        start_api()
        queue.put(ServiceStarted("Inference Gateway API", detail="Listening on port 8001"))
    except Exception as e:
        queue.put(ServiceFailed("Inference Gateway API", error=str(e)))


def run_orchestration_gateway(queue):

    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    # Helper to inject paths mimicking orchestrator.sh
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # Add services/orchestration/app to sys.path
    app_path = os.path.join(base_dir, "services/orchestration/app")
    # Add gateways/orchestration_gateway to sys.path
    gateway_path = os.path.join(base_dir, "gateways/orchestration_gateway")
    
    sys.path.insert(0, app_path)
    sys.path.insert(0, gateway_path)

    try:
        queue.put(ServiceStarting("Orchestration Gateway API"))
        from inferia.gateways.orchestration_gateway.main import start_api
        start_api()
        queue.put(ServiceStarted("Orchestration Gateway API", detail="Listening on port 8080"))
    except Exception as e:
        queue.put(ServiceFailed("Orchestration Gateway API", error=str(e)))
        


def run_worker(queue):

    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    # Helper to inject paths mimicking orchestrator.sh
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # Add services/orchestration/app to sys.path
    app_path = os.path.join(base_dir, "services/orchestration/app")
    
    sys.path.insert(0, app_path)

    try:
        queue.put(ServiceStarting("Orchestration Worker"))
        import asyncio
        from inferia.services.orchestration.app.services.model_deployment.worker_main import main
        asyncio.run(main())
        queue.put(ServiceStarted("Orchestration Worker", detail="Connected to message broker"))
    except Exception as e:
        queue.put(ServiceFailed("Orchestration Worker", error=str(e)))

def run_nosana_sidecar(queue):
    """
    Runs the DePIN Sidecar (Node.js service).
    """
    from inferia.startup_events import ServiceStarting, ServiceStarted, ServiceFailed
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sidecar_dir = os.path.join(base_dir, "services/orchestration/app/services/depin-sidecar")
    
    print(f"[DePIN] Starting Sidecar from {sidecar_dir}")
    
    # Check if directory exists
    if not os.path.isdir(sidecar_dir):
        print(f"[DePIN] Error: Sidecar directory not found at {sidecar_dir}")
        return

    node_modules = os.path.join(sidecar_dir, "node_modules")
    
    try:
        # We use subprocess.run to execute the sidecar
        queue.put(ServiceStarting("nosana-sidecar"))
        if not os.path.isdir(node_modules):
            print("[DePIN] Installing dependencies...")
            subprocess.run(["npm", "install"], cwd=sidecar_dir, check=True)

        print("[DePIN] Launching sidecar...")
        subprocess.Popen(["npx", "tsx", "src/server.ts"], cwd=sidecar_dir, stdout=sys.stdout,
            stderr=sys.stderr)
        queue.put(ServiceStarted("nosana-sidecar", "Node.js"))
        
    except FileNotFoundError as e:
        print("[Nosana] Error: 'npx' command not found. Ensure Node.js is installed.")
        queue.put(ServiceFailed("nosana-sidecar", str(e)))
    except KeyboardInterrupt:
        pass 
    except subprocess.CalledProcessError as e:
        print("[DePIN] Sidecar process failed")
        print(e)
        queue.put(ServiceFailed("nosana-sidecar", str(e)))
    except Exception as e:
        print(f"[Nosana] Error: {e}")
        queue.put(ServiceFailed("nosana-sidecar", str(e)))

def run_dashboard(queue):
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
        queue.put(ServiceFailed("Dashboard", f"Directory not found: {dashboard_dir}"))
        return
    
    try:
        queue.put(ServiceStarting("Dashboard"))
        os.chdir(dashboard_dir)
        
        # Create a handler that handles /dashboard/ prefix for React Router
        class SPAHandler(http.server.SimpleHTTPRequestHandler):
            # Add proper MIME types for JS modules
            extensions_map = {
                **http.server.SimpleHTTPRequestHandler.extensions_map,
                '.js': 'application/javascript',
                '.mjs': 'application/javascript',
                '.css': 'text/css',
                '.json': 'application/json',
                '.svg': 'image/svg+xml',
                '.woff': 'font/woff',
                '.woff2': 'font/woff2',
            }

            def translate_path(self, path):
                """
                Standard path translation with SPA fallback.
                """
                # Use standard logic to resolve to local filesystem path
                resolved = super().translate_path(path)
                
                # SPA Routing Logic
                if os.path.exists(resolved):
                    return resolved
                
                # If path doesn't exist:
                # 1. If it looks like an asset (has extension), let it 404
                _, ext = os.path.splitext(path)
                if ext and ext.lower() not in ['.html', '.htm']:
                    return resolved  # Will result in 404
                
                # 2. Otherwise assume it's a client-side route -> serve index.html
                return os.path.join(os.getcwd(), 'index.html')
            
            def log_message(self, format, *args):
                # Suppress request logs
                pass
        
        with socketserver.TCPServer(("", port), SPAHandler) as httpd:
            print(f"[Dashboard] Serving at http://localhost:{port}/")
            queue.put(ServiceStarted("Dashboard", f"http://localhost:{port}/"))
            httpd.serve_forever()
            
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"[Dashboard] Port {port} already in use")
            queue.put(ServiceFailed("Dashboard", f"Port {port} already in use"))
        else:
            queue.put(ServiceFailed("Dashboard", str(e)))
    except Exception as e:
        print(f"[Dashboard] Error: {e}")
        queue.put(ServiceFailed("Dashboard", str(e)))

def run_init():
    from inferia.cli_init import init_databases
    init_databases()
    

def _run_target(target):
    target()

def run_orchestration_stack():
    """
    Runs the full orchestration stack: API, Worker, and DePIN Sidecar.
    """
    # Create a dummy queue for the processes that expect it
    queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(target=run_orchestration_gateway, name="orchestration-api", args=(queue,)),
        multiprocessing.Process(target=run_worker, name="orchestration-worker", args=(queue,)),
        multiprocessing.Process(target=run_nosana_sidecar, name="nosana-sidecar", args=(queue,)),
    ]

    print("[CLI] Starting Orchestration Stack (API, Worker, DePIN Sidecar)...")
    for p in processes:
        p.start()

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\\n[CLI] Shutting down Orchestration Stack...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join()

def run_all():
    # Run all services efficiently by spawning them as direct children
    queue = multiprocessing.Queue()
    ui = StartupUI(queue, total=6)
    # ui.run()

    processes = [
        # Orchestration Stack
        multiprocessing.Process(target=run_orchestration_gateway, name="orchestration-api", args=(queue,),),
        multiprocessing.Process(target=run_worker, name="orchestration-worker", args=(queue,),),
        multiprocessing.Process(target=run_nosana_sidecar, name="nosana-sidecar", args=(queue,),),
        
        # Inference & Filtration
        multiprocessing.Process(target=run_inference_gateway, name="inference", args=(queue,),),
        multiprocessing.Process(target=run_filtration_gateway, name="filtration", args=(queue,),),
        
        # Dashboard
        multiprocessing.Process(target=run_dashboard, name="dashboard", args=(queue,),),
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
        prog="inferia",
        description="Inferia CLI – distributed inference & orchestration platform",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", help="Inferia command")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize Inferia databases")

    # -----------------
    # Gateways
    # -----------------
    sub.add_parser("filtration-gateway", add_help=False)
    sub.add_parser("inference-gateway", add_help=False)
    sub.add_parser("orchestration-gateway", add_help=False)
    sub.add_parser("api-start", help="Start all services (orchestration, inference, filtration)")

    args, unknown = parser.parse_known_args(argv)
    cmd = args.command
    flags = set(unknown)

    if cmd not in KNOWN_COMMANDS:
        print(f"Unknown command: {cmd}")
        print("Use 'inferia --help' to see available commands.")
        sys.exit(1)

    try:
        if args.command == "filtration-gateway":
            if wants_help(flags):
                show_filtration_docs()
                return
            # Run standalone without queue
            from inferia.gateways.filtration_gateway.main import start_api
            start_api()

        elif args.command == "orchestration-gateway":
            if wants_help(flags):
                show_orchestration_docs()
                return
            run_orchestration_stack()

        elif args.command == "inference-gateway":
            if wants_help(flags):
                show_inference_docs()
                return
            # Run standalone without queue
            from inferia.gateways.inference_gateway.main import start_api
            start_api()

        elif args.command == "init":
            run_init()

        elif args.command == "api-start":
            if wants_help(flags):
                show_inferia()
                return
            run_all()

        else :
            print(f"Unknown command: {args.command}")
            print("Use 'inferia --help' to see available commands.")
            sys.exit(1)

    except KeyboardInterrupt:
        print("Shutting down Inferia...")
        sys.exit(0)


if __name__ == "__main__":
    main()
