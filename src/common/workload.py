"""Shared per-request workload classes and their server-owned priorities.

These are intentionally distinct from orchestration's deployment-level
``workload_type``.  The edge chooses a class from the request route and policy;
clients never supply the priority that reaches a data-plane router.
"""

from enum import Enum


class WorkloadClass(str, Enum):
    REALTIME_AUDIO = "realtime_audio"
    INTERACTIVE_CHAT = "interactive_chat"
    LONG_CONTEXT = "long_context"
    EMBEDDING = "embedding"
    VIDEO_JOB = "video_job"
    BATCH = "batch"


_PRIORITY = {
    WorkloadClass.REALTIME_AUDIO: 100,
    WorkloadClass.INTERACTIVE_CHAT: 80,
    WorkloadClass.LONG_CONTEXT: 60,
    WorkloadClass.EMBEDDING: 50,
    WorkloadClass.VIDEO_JOB: 30,
    WorkloadClass.BATCH: 10,
}


def priority_for(workload_class: WorkloadClass) -> int:
    """Return the fixed default priority; policy may only lower it downstream."""
    return _PRIORITY[workload_class]
