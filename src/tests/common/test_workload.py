from common.workload import WorkloadClass, priority_for


def test_workload_classes_have_stable_priorities():
    assert priority_for(WorkloadClass.REALTIME_AUDIO) == 100
    assert priority_for(WorkloadClass.INTERACTIVE_CHAT) == 80
    assert priority_for(WorkloadClass.LONG_CONTEXT) == 60
    assert priority_for(WorkloadClass.EMBEDDING) == 50
    assert priority_for(WorkloadClass.VIDEO_JOB) == 30
    assert priority_for(WorkloadClass.BATCH) == 10
