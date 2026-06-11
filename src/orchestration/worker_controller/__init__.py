"""
Control-plane side of the inferia-worker protocol.

Replaces the deleted services/{node-agent, llmd_runtime, llmd, compute_node}
folders. The worker_controller maintains live WebSocket connections to
direct-managed GPU workers (kind='worker' in compute_nodes), accepts
heartbeats, and issues LoadModel / UnloadModel commands.

Public surface:
- WorkerAuth          — issue + verify worker JWTs and bootstrap tokens
- WorkerRegistry      — in-memory node_id → connection mapping
- WorkerController    — load_model / unload_model entry points used by the
                        model_deployment service
- protocol            — Pydantic message models shared with the Go worker
"""

from .auth import WorkerAuth, WorkerClaims, BootstrapClaims  # noqa: F401
from .registry import WorkerRegistry, WorkerConn  # noqa: F401
from . import protocol  # noqa: F401
