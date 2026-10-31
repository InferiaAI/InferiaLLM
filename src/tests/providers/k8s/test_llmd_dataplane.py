"""Apply, readiness and delete for the llm-d data-plane mode (session-3 T2).

Runs against an in-memory fake of the kubernetes client (fake_k8s.py) that
raises the client's real 404/409 ApiExceptions. Behaviour on a live cluster is
covered by test_llmd_kind_integration.py, which skips without one.
"""
import asyncio

import pytest

from providers.k8s.llmd import resources as r
from providers.k8s.llmd.dataplane import LlmdDataplane

from .fake_k8s import fake_apis

NS = "inferia-llmd"
NAME = "inferia-llmd-abc123"


def _spec():
    return r.spec_from_metadata(
        name=NAME, namespace=NS, pool_id="p",
        metadata={"engine": r.SIM_ENGINE, "model_id": "sim-model"},
    )


def _dp():
    c, core, apps, custom = fake_apis()
    return c, LlmdDataplane(core=core, apps=apps, custom=custom, poll_seconds=0.001)


def run(coro):
    return asyncio.run(coro)


# --- apply -------------------------------------------------------------------
def test_apply_creates_every_object():
    c, dp = _dp()
    run(dp.apply(_spec()))
    expected = {(o["kind"], NS, o["metadata"]["name"]) for o in r.build_objects(_spec())}
    assert set(c.objects) == expected


def test_apply_failure_rolls_back_everything_created():
    c, dp = _dp()
    c.fail_create.add("InferencePool")  # fails midway through the set
    with pytest.raises(Exception):
        run(dp.apply(_spec()))
    assert c.objects == {}


def test_apply_conflict_is_an_error_not_an_overwrite():
    # Names are random per deployment, so an existing object means something
    # else owns it: never adopt or overwrite it, and never delete it on rollback.
    c, dp = _dp()
    foreign = {"metadata": {"name": r.epp_config_name(NAME)}, "data": {"x": "mine"}}
    c.objects[("ConfigMap", NS, r.epp_config_name(NAME))] = foreign
    with pytest.raises(Exception):
        run(dp.apply(_spec()))
    assert c.objects == {("ConfigMap", NS, r.epp_config_name(NAME)): foreign}


# --- readiness ---------------------------------------------------------------
def test_status_missing_when_nothing_exists():
    _, dp = _dp()
    assert run(dp.status(NAME, NS)) == ("missing", "")


def test_status_pending_until_model_and_router_are_up():
    c, dp = _dp()
    run(dp.apply(_spec()))
    assert run(dp.status(NAME, NS))[0] == "pending"
    c.set_status("Deployment", NS, r.model_server_name(NAME), ready_replicas=1)
    assert run(dp.status(NAME, NS))[0] == "pending"  # router not available yet
    c.set_status("Deployment", NS, r.epp_name(NAME), available_replicas=1)
    assert run(dp.status(NAME, NS)) == ("ready", "")


def test_status_router_alone_is_not_ready():
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.set_status("Deployment", NS, r.epp_name(NAME), available_replicas=1)
    assert run(dp.status(NAME, NS))[0] == "pending"


@pytest.mark.parametrize("reason", ["ErrImagePull", "ImagePullBackOff", "InvalidImageName"])
def test_status_failed_on_a_pull_that_will_not_succeed(reason):
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.add_pod(NS, {r.INSTANCE_LABEL: NAME}, waiting_reason=reason, message="no such image")
    state, detail = run(dp.status(NAME, NS))
    assert state == "failed"
    assert reason in detail


def test_status_still_pending_while_pulling():
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.add_pod(NS, {r.INSTANCE_LABEL: NAME}, waiting_reason="ContainerCreating")
    assert run(dp.status(NAME, NS))[0] == "pending"


# --- delete ------------------------------------------------------------------
def test_delete_removes_everything_and_verifies():
    c, dp = _dp()
    run(dp.apply(_spec()))
    run(dp.delete(NAME, NS))
    assert c.objects == {}


def test_delete_is_idempotent():
    c, dp = _dp()
    run(dp.delete(NAME, NS))          # nothing there: fine
    run(dp.apply(_spec()))
    run(dp.delete(NAME, NS))
    run(dp.delete(NAME, NS))          # already gone: fine
    assert c.objects == {}


def test_delete_removes_the_entry_point_first():
    # Stop new traffic before tearing down what serves it.
    c, dp = _dp()
    run(dp.apply(_spec()))
    run(dp.delete(NAME, NS))
    deletes = [(k, n) for op, k, n in c.calls if op == "delete"]
    assert deletes[0] == ("Service", r.gateway_service_name(NAME))


def test_delete_waits_for_objects_that_take_time_to_go():
    # Kubernetes deletes asynchronously: a Deployment stays visible while its
    # pods terminate. That is not a failure.
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.slow_delete[("Deployment", NS, r.model_server_name(NAME))] = 3
    run(dp.delete(NAME, NS, verify_timeout=5))
    assert c.objects == {}


def test_delete_raises_when_something_survives():
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.sticky_delete.add(("Deployment", NS, r.model_server_name(NAME)))
    with pytest.raises(RuntimeError, match=r.model_server_name(NAME)):
        run(dp.delete(NAME, NS, verify_timeout=0.05))


def test_delete_only_touches_this_instance():
    c, dp = _dp()
    other = r.spec_from_metadata(name="inferia-llmd-zzz999", namespace=NS, pool_id="p",
                                 metadata={"engine": r.SIM_ENGINE, "model_id": "m"})
    run(dp.apply(_spec()))
    run(dp.apply(other))
    run(dp.delete(NAME, NS))
    assert c.objects and all(n.startswith("inferia-llmd-zzz999") for _, _, n in c.objects)


# --- error paths ---------------------------------------------------------------
def _raiser(status):
    from kubernetes.client.exceptions import ApiException

    def fn(**_):
        raise ApiException(status=status, reason="x")
    return fn


def test_status_propagates_api_errors_other_than_404():
    # A 403/500 is not "missing": reporting STOPPED on it would make the
    # liveness worker tear down a healthy deployment.
    c, dp = _dp()
    run(dp.apply(_spec()))
    dp.apps.read_namespaced_deployment = _raiser(500)
    with pytest.raises(Exception):
        run(dp.status(NAME, NS))


def test_status_pending_when_router_missing():
    c, dp = _dp()
    run(dp.apply(_spec()))
    c.set_status("Deployment", NS, r.model_server_name(NAME), ready_replicas=1)
    del c.objects[("Deployment", NS, r.epp_name(NAME))]
    assert run(dp.status(NAME, NS)) == ("pending", "router missing")


def test_unreadable_pods_are_not_a_failure():
    c, dp = _dp()
    run(dp.apply(_spec()))

    def boom(**_):
        raise RuntimeError("list failed")
    dp.core.list_namespaced_pod = boom
    assert run(dp.status(NAME, NS))[0] == "pending"


def test_rollback_keeps_going_when_a_delete_fails_and_reraises_the_cause():
    c, dp = _dp()
    c.fail_create.add("InferencePool")
    dp.core.delete_namespaced_service = _raiser(500)
    with pytest.raises(Exception, match="boom"):
        run(dp.apply(_spec()))
    # Services could not be removed; everything else created was.
    assert {k for k, _, _ in c.objects} == {"Service"}


def test_delete_propagates_api_errors_other_than_404():
    _, dp = _dp()
    dp.core.delete_namespaced_service = _raiser(403)
    with pytest.raises(Exception):
        run(dp.delete(NAME, NS))
