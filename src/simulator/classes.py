"""Workload classes (docs/platform/interfaces.md §3).

Mirrored here until Session 3 lands the shared ``src/common/workload.py``;
a test pins the values and priorities to the interface table.
"""

from __future__ import annotations

from enum import Enum


class WorkloadClass(str, Enum):
    REALTIME_AUDIO = "realtime_audio"
    INTERACTIVE_CHAT = "interactive_chat"
    LONG_CONTEXT = "long_context"
    EMBEDDING = "embedding"
    VIDEO_JOB = "video_job"
    BATCH = "batch"


# Higher wins (interfaces §3).
PRIORITY: dict[WorkloadClass, int] = {
    WorkloadClass.REALTIME_AUDIO: 100,
    WorkloadClass.INTERACTIVE_CHAT: 80,
    WorkloadClass.LONG_CONTEXT: 60,
    WorkloadClass.EMBEDDING: 50,
    WorkloadClass.VIDEO_JOB: 30,
    WorkloadClass.BATCH: 10,
}
