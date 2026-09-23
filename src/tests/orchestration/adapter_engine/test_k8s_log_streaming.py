"""Tests for Kubernetes log streaming.

The follow stream from the kubernetes client blocks, so it is read on a thread
and handed back through a queue. These cover that bridge: lines arrive in
order, the response is closed when the caller stops, and an instance with no
pod fails rather than hanging.

The kubernetes client is mocked; streaming from a live pod is integration
territory and not reachable from CI.
"""
from unittest.mock import MagicMock, patch

import pytest

from providers.k8s.k8s_adapter import KubernetesAdapter


def _adapter():
    with patch("providers.k8s.k8s_adapter.config"):
        a = KubernetesAdapter()
    a.core = MagicMock()
    a.apps = MagicMock()
    return a


def _pods(*names):
    listing = MagicMock()
    listing.items = []
    for n in names:
        pod = MagicMock()
        pod.metadata.name = n
        listing.items.append(pod)
    return listing


def _response(*chunks):
    """A stand-in for the client's raw response."""
    resp = MagicMock()
    resp.stream.return_value = iter(chunks)
    return resp


# ---------------------------------------------------------------------------
# What the dashboard is told
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_streaming_info_is_supported():
    info = await _adapter().get_log_streaming_info(
        provider_instance_id="inferia-worker-abc"
    )
    assert info["supported"] is True
    assert info["ws_url"] == "/v1/deployment/ws"
    assert info["subscription"] == {
        "type": "subscribe_logs",
        "provider": "k8s",
        "instance": "inferia-worker-abc",
        "namespace": "default",
    }


@pytest.mark.asyncio
async def test_streaming_info_names_the_instance_not_the_pod():
    # A StatefulSet replaces its pod, so a pod name would go stale.
    info = await _adapter().get_log_streaming_info(
        provider_instance_id="inferia-worker-abc"
    )
    assert info["subscription"]["instance"] == "inferia-worker-abc"
    assert "pod_name" not in info["subscription"]


# ---------------------------------------------------------------------------
# The stream itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_yields_lines_in_order():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods("inferia-worker-abc-0")
    a.core.read_namespaced_pod_log.return_value = _response(
        b"first\nsecond\n", b"third\n"
    )

    got = [line async for line in a.stream_logs(instance="inferia-worker-abc")]
    assert got == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_splits_lines_across_chunks():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods("inferia-worker-abc-0")
    a.core.read_namespaced_pod_log.return_value = _response(b"one\ntwo\nthree\n")

    got = [line async for line in a.stream_logs(instance="inferia-worker-abc")]
    assert got == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_closes_the_response_when_the_caller_stops():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods("inferia-worker-abc-0")
    resp = _response(b"a\n", b"b\n", b"c\n")
    a.core.read_namespaced_pod_log.return_value = resp

    stream = a.stream_logs(instance="inferia-worker-abc")
    assert await stream.__anext__() == "a"
    await stream.aclose()

    resp.close.assert_called_once()
    resp.release_conn.assert_called_once()


@pytest.mark.asyncio
async def test_follows_the_named_pod():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods("inferia-worker-abc-0")
    a.core.read_namespaced_pod_log.return_value = _response(b"x\n")

    [line async for line in a.stream_logs(instance="inferia-worker-abc")]

    kwargs = a.core.read_namespaced_pod_log.call_args.kwargs
    assert kwargs["name"] == "inferia-worker-abc-0"
    assert kwargs["follow"] is True
    assert kwargs["_preload_content"] is False


@pytest.mark.asyncio
async def test_no_pod_raises_rather_than_hanging():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods()

    with pytest.raises(RuntimeError, match="no pod found"):
        async for _ in a.stream_logs(instance="inferia-worker-abc"):
            pass


@pytest.mark.asyncio
async def test_undecodable_bytes_do_not_kill_the_stream():
    a = _adapter()
    a.core.list_namespaced_pod.return_value = _pods("inferia-worker-abc-0")
    a.core.read_namespaced_pod_log.return_value = _response(b"\xff\xfe bad\n", b"good\n")

    got = [line async for line in a.stream_logs(instance="inferia-worker-abc")]
    assert got[-1] == "good"
    assert len(got) == 2
