import multiprocessing
import sys
import os
import subprocess
import time
import signal

def run_api():
    """Runs the Orchestration Gateway API (FastAPI) via uvicorn programmatically."""
    # Add paths so imports work
    base_dir = Path("/app")
    sys.path.insert(0, str(base_dir / "package/src/inferia/services/orchestration/app"))
    sys.path.insert(0, str(base_dir / "apps/orchestration-gateway"))
    
    # We invoke the serve function from app.py
    # But app.py has `if __name__ == "__main__": asyncio.run(serve())`
    # We can import it and run it.
    from apps.orchestration_gateway.app import serve
    import asyncio
    asyncio.run(serve())

def run_api_subprocess():
    """Runs API as a subprocess to keep it isolated/clean."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app/package/src/inferia/services/orchestration/app:/app/apps/orchestration-gateway:/app"
    cmd = ["python", "apps/orchestration-gateway/app.py"]
    subprocess.run(cmd, env=env, check=True)

def run_worker_subprocess():
    """Runs the Worker as a subprocess."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app/package/src/inferia/services/orchestration/app:/app"
    cmd = ["python", "package/src/inferia/services/orchestration/app/services/model_deployment/worker_main.py"]
    subprocess.run(cmd, env=env, check=True)

def run_sidecar_subprocess():
    """Runs the Node.js Sidecar."""
    # Locating the sidecar directory inside the container
    sidecar_dir = "/app/package/src/inferia/services/orchestration/app/services/depin-sidecar"
    
    # Install dependencies if missing (safety check, though Docker build should handle it)
    if not os.path.exists(os.path.join(sidecar_dir, "node_modules")):
         print("[Stack] Installing sidecar dependencies...")
         subprocess.run(["npm", "install"], cwd=sidecar_dir, check=True)
    
    print("[Stack] Starting DePIN Sidecar...")
    # Using npx tsx to run the typescript server directly
    cmd = ["npx", "tsx", "src/server.ts"]
    subprocess.run(cmd, cwd=sidecar_dir, check=True)

def main():
    processes = [
        multiprocessing.Process(target=run_api_subprocess, name="api"),
        multiprocessing.Process(target=run_worker_subprocess, name="worker"),
        multiprocessing.Process(target=run_sidecar_subprocess, name="sidecar"),
    ]

    print("[Stack] Starting Orchestration Stack in Docker...")
    for p in processes:
        p.start()
        
    try:
        # Monitor processes. If any dies, we shut down the whole container.
        while True:
            time.sleep(1)
            for p in processes:
                if not p.is_alive():
                    print(f"[Stack] Process {p.name} died. Shutting down container.")
                    for kill_p in processes:
                        if kill_p.is_alive():
                            kill_p.terminate()
                    sys.exit(1)
    except KeyboardInterrupt:
        print("\n[Stack] Shutting down...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join()

if __name__ == "__main__":
    main()
