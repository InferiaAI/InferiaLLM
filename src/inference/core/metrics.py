"""Prometheus metrics for the inference path.

Scraped from ``/metrics`` on the inference app. Emitted here rather than in
api_gateway because inference requests never reach the gateway: unified_web
mounts the gateway at ``/api`` and this service at ``/inf``.
"""

from prometheus_client import Counter, Gauge, Histogram

# 2.0 is an edge because the SLA target is "P95 TTFT under two seconds", and a
# percentile on a bucket edge is read off exactly rather than interpolated.
_TTFT_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)

# The default buckets stop at 10s, which drops most real completions into +Inf.
_DURATION_BUCKETS = (0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)


requests_total = Counter(
    "inferia_inference_requests_total",
    "Completed inference requests.",
    ["deployment", "model", "request_type", "status_code"],
)

duration_seconds = Histogram(
    "inferia_inference_duration_seconds",
    "End-to-end inference request duration.",
    ["deployment", "model", "request_type"],
    buckets=_DURATION_BUCKETS,
)

# A non-streaming request has no first token, so it records nothing here.
# Substituting total latency is the bug behind #304.
ttft_seconds = Histogram(
    "inferia_inference_ttft_seconds",
    "Time to first token. Streaming requests only.",
    ["deployment", "model"],
    buckets=_TTFT_BUCKETS,
)

in_flight = Gauge(
    "inferia_upstream_in_flight_requests",
    "Requests currently being served by an engine.",
    ["deployment"],
)

queued = Gauge(
    "inferia_upstream_queued_requests",
    "Requests waiting for an upstream concurrency slot.",
    ["deployment"],
)


def observe_request(
    *,
    deployment_id,
    model,
    request_type,
    status_code,
    duration_seconds_value,
    ttft_ms=None,
):
    """Record one completed request.

    Never raises. A metrics backend problem must not fail a request that has
    already been served.
    """
    try:
        deployment = str(deployment_id or "none")
        model_label = str(model or "unknown")

        requests_total.labels(
            deployment=deployment,
            model=model_label,
            request_type=request_type,
            status_code=str(status_code),
        ).inc()

        duration_seconds.labels(
            deployment=deployment,
            model=model_label,
            request_type=request_type,
        ).observe(duration_seconds_value)

        if ttft_ms is not None:
            ttft_seconds.labels(
                deployment=deployment, model=model_label,
            ).observe(ttft_ms / 1000.0)
    except Exception:  # pragma: no cover - defensive
        pass
