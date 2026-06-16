"""
Pydantic models for the worker control-channel protocol.

These mirror the Go types in inferia-worker/internal/control/protocol.go. Changes
to either side require updating the other. Add a contract test if you grow
this surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Register (HTTP, not WS) -------------------------------------------------


class RegisterRequest(BaseModel):
    node_name: str
    pool_id: str
    advertise_url: str
    allocatable: dict[str, str] = Field(default_factory=dict)
    # Optional bootstrap_token in request body (DB-backed single-use token).
    # When provided, the Authorization header is not required.
    # JWT-encoded bootstrap tokens minted by /api/v1/nodes/add/worker are
    # ~300 chars (HS256 header + claims + signature, base64url). The
    # previous 128-char cap predated the JWT migration and silently
    # rejected every register call. Bound at 4096 to leave headroom for
    # additional claims without re-tuning.
    bootstrap_token: Optional[str] = Field(default=None, min_length=10, max_length=4096)
    # Optional cloud-env metadata fields recorded in inventory labels.
    runtime_env: Optional[str] = Field(default=None, max_length=64)
    instance_id: Optional[str] = Field(default=None, max_length=128)
    region: Optional[str] = Field(default=None, max_length=64)
    availability_zone: Optional[str] = Field(default=None, max_length=64)


class RegisterResponse(BaseModel):
    node_id: str
    worker_jwt: str


# --- Envelope + message bodies (WS) ------------------------------------------


MessageType = Literal[
    "Hello", "Heartbeat", "LoadModel", "UnloadModel", "CommandResult", "Ping",
    # Shell + logs stream multiplexing over the worker→CP channel.
    # CP → worker (control side):
    "ShellOpen", "ShellInput", "ShellResize", "ShellClose",
    "LogsOpen", "LogsClose",
    # worker → CP (data side):
    "ShellOutput", "ShellExit", "ShellError",
    "LogsLine", "LogsEnd",
]


class Envelope(BaseModel):
    type: MessageType
    id: str = ""
    ts: str = ""
    body: Any = None


class HelloBody(BaseModel):
    server_time: datetime
    channel_id: str


class HeartbeatEvent(BaseModel):
    type: str
    deployment_id: str
    exit_code: int = 0
    reason: str = ""


class EngineMetrics(BaseModel):
    """Arbitrary key-value pairs scraped from the inference engine (e.g. vLLM)."""
    model_config = {"extra": "allow"}
    # Known keys are typed; anything else comes through as int via extra.
    vllm_num_requests_running: int | None = Field(default=None, alias="vllm:num_requests_running")
    vllm_request_success_total: int | None = Field(default=None, alias="vllm:request_success_total")


class DeployMetric(BaseModel):
    deployment_id: str
    recipe: str
    model: str = ""
    requests_total: int = 0
    active_requests: int = 0
    request_latency_p50_ms: float = 0.0
    request_latency_p95_ms: float = 0.0
    pull_duration_ms: int = 0
    start_duration_ms: int = 0
    phase: str = ""
    engine_metrics: EngineMetrics = Field(default_factory=EngineMetrics)


class GPUSample(BaseModel):
    index: int = 0
    name: str = ""
    util_pct: float = 0.0
    mem_used_mib: int = 0
    mem_total_mib: int = 0


class MetricsSample(BaseModel):
    ts: str = ""
    cpu_pct: float = 0.0
    mem_used_bytes: int = 0
    mem_total_bytes: int = 0
    net_rx_bps: float = 0.0
    net_tx_bps: float = 0.0
    disk_read_bps: float = 0.0
    disk_write_bps: float = 0.0
    gpus: list[GPUSample] = Field(default_factory=list)


class HeartbeatBody(BaseModel):
    used: dict[str, str] = Field(default_factory=dict)
    loaded_models: list[str] = Field(default_factory=list)
    events: list[HeartbeatEvent] = Field(default_factory=list)
    deploy_metrics: list[DeployMetric] = Field(default_factory=list)
    metrics: Optional[MetricsSample] = None


class ModelRef(BaseModel):
    artifact_uri: str
    format: str = ""
    backend: str = ""


class LoadModelBody(BaseModel):
    deployment_id: str
    recipe: str
    model: ModelRef
    config: dict[str, Any] = Field(default_factory=dict)
    gpu_indices: list[int] = Field(default_factory=list)
    port: int = 0
    env: dict[str, str] = Field(default_factory=dict)


class UnloadModelBody(BaseModel):
    deployment_id: str


class CommandResultBody(BaseModel):
    in_reply_to: str
    status: Literal["ok", "failed"]
    detail: str = ""
    endpoint_url: str = ""


# --- Shell + logs stream multiplexing ---------------------------------------
#
# A single worker→CP control channel carries many concurrent shell + logs
# sessions, each identified by a ``stream_id`` minted by the CP. Frames flow
# in both directions; the channel read loop on each end dispatches by type
# and routes to the appropriate session.
#
# Field caps mirror the conservative limits in the bootstrap_builder: keep
# any single envelope under ~1 MiB so the WS connection isn't stalled by
# one giant frame. Worker-side PTY output is chunked to ``data`` <= 64 KiB
# by the worker; dashboards send single-line stdin in similar amounts.


class ShellOpenBody(BaseModel):
    """CP→worker: spawn an interactive shell session.

    The worker exec's ``shell`` in the target ``container`` (if given) or
    on the host. ``user`` switches uid via the same mechanism the legacy
    /v1/shell endpoint uses (e.g. ``"root"`` or ``"1000:1000"``).
    ``cols``/``rows`` set the initial PTY window size.
    """

    stream_id: str
    shell: str = "/bin/sh"
    user: str = ""
    deployment_id: str = ""
    container_id: str = ""
    cols: int = 0
    rows: int = 0


class ShellInputBody(BaseModel):
    """CP→worker: write bytes to the running shell's stdin."""

    stream_id: str
    data: str  # raw stdin bytes (may include control chars like ^C)


class ShellResizeBody(BaseModel):
    """CP→worker: resize the shell's PTY window."""

    stream_id: str
    cols: int
    rows: int


class ShellCloseBody(BaseModel):
    """CP→worker: kill the shell and tear down the session.

    Idempotent — worker ignores unknown stream_ids.
    """

    stream_id: str


class ShellOutputBody(BaseModel):
    """worker→CP: a chunk of PTY output (stdout+stderr merged)."""

    stream_id: str
    data: str


class ShellExitBody(BaseModel):
    """worker→CP: the shell process exited cleanly."""

    stream_id: str
    exit_code: int = 0
    reason: str = ""


class ShellErrorBody(BaseModel):
    """worker→CP: failed to spawn the shell or PTY died abnormally.

    Sent in lieu of (not in addition to) ShellExit.
    """

    stream_id: str
    message: str


class LogsOpenBody(BaseModel):
    """CP→worker: stream container logs for ``deployment_id`` / ``container_id``.

    When both are empty the worker tails its first running container. Lines
    flow back as LogsLine envelopes until the dashboard sends LogsClose
    (or the container exits, which emits LogsEnd).
    """

    stream_id: str
    deployment_id: str = ""
    container_id: str = ""


class LogsLineBody(BaseModel):
    """worker→CP: one line of container output."""

    stream_id: str
    stream: Literal["stdout", "stderr"] = "stdout"
    data: str


class LogsEndBody(BaseModel):
    """worker→CP: log stream ended (container stopped or follow timed out)."""

    stream_id: str
    reason: str = ""


class LogsCloseBody(BaseModel):
    """CP→worker: stop streaming logs for this session.

    Idempotent — worker ignores unknown stream_ids.
    """

    stream_id: str


__all__ = [
    "RegisterRequest",
    "RegisterResponse",
    "Envelope",
    "MessageType",
    "HelloBody",
    "HeartbeatBody",
    "HeartbeatEvent",
    "DeployMetric",
    "EngineMetrics",
    "GPUSample",
    "MetricsSample",
    "LoadModelBody",
    "UnloadModelBody",
    "CommandResultBody",
    "ModelRef",
    # Shell + logs tunnel envelopes
    "ShellOpenBody",
    "ShellInputBody",
    "ShellResizeBody",
    "ShellCloseBody",
    "ShellOutputBody",
    "ShellExitBody",
    "ShellErrorBody",
    "LogsOpenBody",
    "LogsLineBody",
    "LogsEndBody",
    "LogsCloseBody",
]
