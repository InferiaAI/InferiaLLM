from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ServiceStatus(str, Enum):
    STARTING = "starting"
    STARTED = "started"
    FAILED = "failed"


@dataclass
class StartupEvent:
    service: str
    status: ServiceStatus
    detail: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


class ServiceStarting(StartupEvent):
    def __init__(self, service: str):
        super().__init__(service, ServiceStatus.STARTING)


class ServiceStarted(StartupEvent):
    def __init__(self, service: str, detail: str = ""):
        super().__init__(service, ServiceStatus.STARTED, detail=detail)


class ServiceFailed(StartupEvent):
    def __init__(self, service: str, error: str):
            super().__init__(service, ServiceStatus.FAILED, error=error)