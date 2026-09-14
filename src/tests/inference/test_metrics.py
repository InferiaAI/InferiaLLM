"""Tests for the Prometheus metrics on the inference path.

Two things are being protected here. Time to first token must stay absent for
non-streaming requests, because filling it in with total latency is the bug
behind #304. And the queue gauge must be incremented before a slot is acquired,
because that ordering is the whole reason it measures a queue.
"""

import asyncio
import pytest
from unittest.mock import patch
from prometheus_client import REGISTRY

from fastapi import HTTPException

from inference.core import metrics
from inference.core.concurrency_limiter import UpstreamConcurrencyLimiter


def _gauge(name, deployment):
    """Current value of a labelled gauge, 0.0 when never touched."""
    value = REGISTRY.get_sample_value(name, {"deployment": deployment})
    return 0.0 if value is None else value


def _queued(deployment):
    return _gauge("inferia_upstream_queued_requests", deployment)


def _in_flight(deployment):
    return _gauge("inferia_upstream_in_flight_requests", deployment)


def _limiter(per_deployment=1, timeout=5.0):
    with patch("inference.core.concurrency_limiter.settings") as s:
        s.upstream_global_max_in_flight = 0
        s.upstream_per_deployment_max_in_flight = per_deployment
        s.upstream_slot_acquire_timeout_seconds = timeout
        return UpstreamConcurrencyLimiter()


# ---------------------------------------------------------------------------
# observe_request
# ---------------------------------------------------------------------------
class TestObserveRequest:
    def test_a_completed_request_is_counted(self):
        metrics.observe_request(
            deployment_id="d-count", model="qwen2:0.5b", request_type="llm",
            status_code=200, duration_seconds_value=1.5,
        )

        total = REGISTRY.get_sample_value(
            "inferia_inference_requests_total",
            {"deployment": "d-count", "model": "qwen2:0.5b",
             "request_type": "llm", "status_code": "200"},
        )
        assert total == 1.0

    def test_duration_is_recorded_in_seconds(self):
        metrics.observe_request(
            deployment_id="d-dur", model="m", request_type="llm",
            status_code=200, duration_seconds_value=2.0,
        )

        total = REGISTRY.get_sample_value(
            "inferia_inference_duration_seconds_sum",
            {"deployment": "d-dur", "model": "m", "request_type": "llm"},
        )
        assert total == 2.0

    def test_ttft_is_converted_from_milliseconds(self):
        metrics.observe_request(
            deployment_id="d-ttft", model="m", request_type="llm",
            status_code=200, duration_seconds_value=9.0, ttft_ms=250.0,
        )

        total = REGISTRY.get_sample_value(
            "inferia_inference_ttft_seconds_sum",
            {"deployment": "d-ttft", "model": "m"},
        )
        assert total == 0.25, "the histogram is in seconds, the caller has ms"

    def test_a_request_without_ttft_records_no_ttft(self):
        """#304. A non-streaming request has no first token, so substituting
        total latency is what made the dashboard figure meaningless."""
        metrics.observe_request(
            deployment_id="d-none", model="m", request_type="llm",
            status_code=200, duration_seconds_value=4.0, ttft_ms=None,
        )

        count = REGISTRY.get_sample_value(
            "inferia_inference_ttft_seconds_count",
            {"deployment": "d-none", "model": "m"},
        )
        assert count is None, "absent is the correct value, not a filled-in one"

        duration = REGISTRY.get_sample_value(
            "inferia_inference_duration_seconds_count",
            {"deployment": "d-none", "model": "m", "request_type": "llm"},
        )
        assert duration == 1.0, "duration is still recorded"

    def test_a_metrics_failure_does_not_reach_the_caller(self):
        """The request has already been served by the time this runs."""
        with patch.object(
            metrics.requests_total, "labels", side_effect=RuntimeError("boom")
        ):
            metrics.observe_request(
                deployment_id="d-boom", model="m", request_type="llm",
                status_code=200, duration_seconds_value=1.0,
            )

    def test_a_missing_deployment_id_does_not_drop_the_sample(self):
        """External deployments have no pool and may carry no id."""
        metrics.observe_request(
            deployment_id=None, model="m", request_type="llm",
            status_code=200, duration_seconds_value=1.0,
        )

        total = REGISTRY.get_sample_value(
            "inferia_inference_requests_total",
            {"deployment": "none", "model": "m",
             "request_type": "llm", "status_code": "200"},
        )
        assert total == 1.0


# ---------------------------------------------------------------------------
# Queue and in-flight gauges
# ---------------------------------------------------------------------------
class TestConcurrencyGauges:
    @pytest.mark.asyncio
    async def test_a_served_request_is_in_flight_and_not_queued(self):
        limiter = _limiter()

        async with limiter.limit("d-serve"):
            assert _in_flight("d-serve") == 1.0
            assert _queued("d-serve") == 0.0, "it holds a slot, it is not waiting"

        assert _in_flight("d-serve") == 0.0

    @pytest.mark.asyncio
    async def test_a_request_without_a_slot_shows_as_queued(self):
        """The assertion the whole metric exists for. With the increment moved
        below the acquire this reads zero and autoscaling never triggers."""
        limiter = _limiter(per_deployment=1)
        released = asyncio.Event()

        async def holder():
            async with limiter.limit("d-wait"):
                await released.wait()

        async def waiter():
            async with limiter.limit("d-wait"):
                pass

        held = asyncio.create_task(holder())
        await asyncio.sleep(0.05)

        blocked = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)

        assert _queued("d-wait") == 1.0, "the second request is waiting"
        assert _in_flight("d-wait") == 1.0, "only the first is being served"

        released.set()
        await asyncio.gather(held, blocked)

        assert _queued("d-wait") == 0.0
        assert _in_flight("d-wait") == 0.0

    @pytest.mark.asyncio
    async def test_a_rejected_request_does_not_leak_the_queue_gauge(self):
        """A 429 leaves the request counted as waiting forever, which would
        hold a scaled-up replica permanently."""
        limiter = _limiter(per_deployment=1, timeout=0.05)
        released = asyncio.Event()

        async def holder():
            async with limiter.limit("d-429"):
                await released.wait()

        held = asyncio.create_task(holder())
        await asyncio.sleep(0.05)

        with pytest.raises(HTTPException) as exc:
            async with limiter.limit("d-429"):
                pass
        assert exc.value.status_code == 429

        assert _queued("d-429") == 0.0, "the rejected request is no longer waiting"

        released.set()
        await held
        assert _in_flight("d-429") == 0.0

    @pytest.mark.asyncio
    async def test_a_failed_request_does_not_leak_the_in_flight_gauge(self):
        limiter = _limiter()

        with pytest.raises(ValueError):
            async with limiter.limit("d-error"):
                raise ValueError("upstream died")

        assert _in_flight("d-error") == 0.0
        assert _queued("d-error") == 0.0

    @pytest.mark.asyncio
    async def test_gauges_work_with_no_limits_configured(self):
        """Both limits default to off, and in-flight must still be measured."""
        limiter = _limiter(per_deployment=0)

        async with limiter.limit("d-nolimit"):
            assert _in_flight("d-nolimit") == 1.0

        assert _in_flight("d-nolimit") == 0.0
