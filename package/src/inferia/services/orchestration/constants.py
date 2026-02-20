from enum import Enum


class NodeState(str, Enum):
    """Valid node states in compute_inventory."""

    ORDERED = "ordered"
    PROVISIONING = "provisioning"
    READY = "ready"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"
    TERMINATED = "terminated"
    OFFLINE = "offline"

    @classmethod
    def from_incoming(cls, state: str) -> str:
        """Map incoming states to valid DB enum values."""
        mapping = {
            "failed": cls.UNHEALTHY.value,
            "completed": cls.TERMINATED.value,
        }
        return mapping.get(state.lower(), state)


class DeploymentState(str, Enum):
    """Valid deployment states."""

    PENDING = "PENDING"
    PROVISIONING = "PROVISIONING"
    SCHEDULING = "SCHEDULING"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"
