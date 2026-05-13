"""
Pydantic models for the worker control-channel protocol.

These mirror the Go types in inferia-worker/internal/control/protocol.go. Changes
to either side require updating the other. Add a contract test if you grow
this surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Register (HTTP, not WS) -------------------------------------------------


class RegisterRequest(BaseModel):
    node_name: str
    pool_id: str
    advertise_url: str
    allocatable: dict[str, str] = Field(default_factory=dict)


class RegisterResponse(BaseModel):
    node_id: str
    worker_jwt: str


# --- Envelope + message bodies (WS) ------------------------------------------


MessageType = Literal[
    "Hello", "Heartbeat", "LoadModel", "UnloadModel", "CommandResult", "Ping"
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


class HeartbeatBody(BaseModel):
    used: dict[str, str] = Field(default_factory=dict)
    loaded_models: list[str] = Field(default_factory=list)
    events: list[HeartbeatEvent] = Field(default_factory=list)


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


__all__ = [
    "RegisterRequest",
    "RegisterResponse",
    "Envelope",
    "MessageType",
    "HelloBody",
    "HeartbeatBody",
    "HeartbeatEvent",
    "LoadModelBody",
    "UnloadModelBody",
    "CommandResultBody",
    "ModelRef",
]
