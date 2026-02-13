from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from inferia.services.filtration.management.insights import (
    get_insights_filters,
    get_insights_logs,
    get_insights_summary,
    get_insights_timeseries,
)
from inferia.services.filtration.schemas.auth import UserContext


def _make_request(with_user: bool = True) -> Request:
    request = Request(
        {"type": "http", "method": "GET", "path": "/management/insights", "headers": []}
    )
    if with_user:
        request.state.user = UserContext(
            user_id="user-1",
            username="user@example.com",
            email="user@example.com",
            roles=["admin"],
            permissions=["deployment:list"],
            org_id="org-123",
            quota_limit=1000,
            quota_used=0,
            is_active=True,
        )
    return request


def _make_log(log_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=log_id,
        deployment_id=str(uuid4()),
        user_id="user-1",
        model="llama3",
        ip_address="203.0.113.10",
        request_payload=None,
        latency_ms=150,
        ttft_ms=60,
        tokens_per_second=31.5,
        prompt_tokens=12,
        completion_tokens=30,
        total_tokens=42,
        status_code=200,
        error_message=None,
        is_streaming=False,
        applied_policies=["guardrail"],
        created_at=datetime.now(timezone.utc),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_insights_summary_requires_authenticated_user():
    request = _make_request(with_user=False)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await get_insights_summary(
            request=request,
            start_time=_now() - timedelta(days=1),
            end_time=_now(),
            deployment_id=None,
            model=None,
            status="all",
            db=db,
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_insights_summary_requires_permission(monkeypatch):
    request = _make_request()
    db = AsyncMock()

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(
        "inferia.services.filtration.management.insights.authz_service.require_permission",
        _deny,
    )

    with pytest.raises(HTTPException) as exc:
        await get_insights_summary(
            request=request,
            start_time=_now() - timedelta(days=1),
            end_time=_now(),
            deployment_id=None,
            model=None,
            status="all",
            db=db,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_summary_query_includes_org_scope_and_status_filter():
    request = _make_request()
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = SimpleNamespace(
        requests=5,
        successful_requests=4,
        failed_requests=1,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        avg_latency_ms=120.0,
    )
    db.execute.return_value = result

    await get_insights_summary(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        ip_address="203.0.113.10",
        status="error",
        db=db,
    )

    stmt = db.execute.call_args.args[0]
    stmt_str = str(stmt)
    assert "model_deployments.org_id" in stmt_str
    assert "inference_logs.status_code >=" in stmt_str
    assert "inference_logs.ttft_ms" in stmt_str
    assert "inference_logs.ip_address" in stmt_str


@pytest.mark.asyncio
async def test_time_filter_validation_and_max_range():
    request = _make_request()
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await get_insights_summary(
            request=request,
            start_time=_now() - timedelta(days=91),
            end_time=_now(),
            deployment_id=None,
            model=None,
            status="all",
            db=db,
        )

    assert exc.value.status_code == 400
    assert "maximum allowed range is 90 days" in exc.value.detail


@pytest.mark.asyncio
async def test_summary_handles_mixed_latency_and_zero_defaults():
    request = _make_request()
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = SimpleNamespace(
        requests=2,
        successful_requests=2,
        failed_requests=0,
        prompt_tokens=20,
        completion_tokens=40,
        total_tokens=60,
        avg_latency_ms=140.5,
        active_duration_ms=2000.0,
        avg_tokens_per_second=20.0,
    )
    db.execute.return_value = result

    response = await get_insights_summary(
        request=request,
        start_time=_now() - timedelta(hours=2),
        end_time=_now(),
        deployment_id=None,
        model=None,
        status="all",
        db=db,
    )

    assert response.totals.requests == 2
    assert response.latency_ms.avg == 140.5
    assert response.throughput.tokens_per_second > 0
    assert response.throughput.avg_tokens_per_second == 20.0


@pytest.mark.asyncio
async def test_timeseries_bucket_ordering_and_granularity():
    request = _make_request()
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        SimpleNamespace(
            bucket_start=datetime(2026, 2, 10, 10, 0, 0),
            requests=4,
            failed_requests=1,
            successful_requests=3,
            prompt_tokens=30,
            completion_tokens=70,
            total_tokens=100,
            avg_latency_ms=90.0,
        ),
        SimpleNamespace(
            bucket_start=datetime(2026, 2, 10, 11, 0, 0),
            requests=2,
            failed_requests=0,
            successful_requests=2,
            prompt_tokens=10,
            completion_tokens=50,
            total_tokens=60,
            avg_latency_ms=80.0,
        ),
    ]
    db.execute.return_value = result

    response = await get_insights_timeseries(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        ip_address="203.0.113.10",
        status="all",
        granularity="hour",
        db=db,
    )

    stmt = db.execute.call_args.args[0]
    stmt_str = str(stmt)
    assert "date_trunc" in stmt_str
    assert "inference_logs.ttft_ms" in stmt_str
    assert "inference_logs.ip_address" in stmt_str
    assert response.granularity == "hour"
    assert len(response.buckets) == 2
    assert response.buckets[0].success_rate == 75.0


@pytest.mark.asyncio
async def test_logs_pagination_and_limit_clamping():
    request = _make_request()
    db = AsyncMock()

    count_result = MagicMock()
    count_result.scalar.return_value = 2

    logs_result = MagicMock()
    logs_result.scalars.return_value.all.return_value = [_make_log("l1"), _make_log("l2")]

    db.execute.side_effect = [count_result, logs_result]

    response = await get_insights_logs(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        ip_address="203.0.113.10",
        status="all",
        limit=999,
        offset=10,
        db=db,
    )

    count_stmt = db.execute.call_args_list[0].args[0]
    logs_stmt = db.execute.call_args_list[1].args[0]
    assert "inference_logs.ip_address" in str(count_stmt)
    assert "inference_logs.ip_address" in str(logs_stmt)

    assert response.pagination.limit == 200
    assert response.pagination.offset == 10
    assert response.pagination.total == 2
    assert len(response.items) == 2


@pytest.mark.asyncio
async def test_empty_dataset_returns_zeroed_shapes():
    request = _make_request()
    db = AsyncMock()

    summary_result = MagicMock()
    summary_result.first.return_value = SimpleNamespace(
        requests=0,
        successful_requests=0,
        failed_requests=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        avg_latency_ms=None,
    )
    db.execute.return_value = summary_result

    summary = await get_insights_summary(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        status="all",
        db=db,
    )
    assert summary.totals.requests == 0
    assert summary.latency_ms.avg == 0.0

    timeseries_result = MagicMock()
    timeseries_result.all.return_value = []
    db.execute.return_value = timeseries_result
    timeseries = await get_insights_timeseries(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        status="all",
        granularity="day",
        db=db,
    )
    assert timeseries.buckets == []

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    logs_result = MagicMock()
    logs_result.scalars.return_value.all.return_value = []
    db.execute.side_effect = [count_result, logs_result]
    logs = await get_insights_logs(
        request=request,
        start_time=_now() - timedelta(days=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        status="all",
        limit=50,
        offset=0,
        db=db,
    )
    assert logs.pagination.total == 0
    assert logs.items == []


@pytest.mark.asyncio
async def test_filters_endpoint_returns_deployments_and_models():
    request = _make_request()
    db = AsyncMock()

    deployments_result = MagicMock()
    deployments_result.all.return_value = [
        SimpleNamespace(id=uuid4(), model_name="llama3"),
        SimpleNamespace(id=uuid4(), model_name="mixtral"),
    ]
    models_result = MagicMock()
    models_result.all.return_value = [
        SimpleNamespace(model="llama3"),
        SimpleNamespace(model="mixtral"),
    ]
    ip_addresses_result = MagicMock()
    ip_addresses_result.all.return_value = [
        SimpleNamespace(ip_address="203.0.113.10"),
        SimpleNamespace(ip_address="203.0.113.11"),
    ]
    db.execute.side_effect = [deployments_result, models_result, ip_addresses_result]

    response = await get_insights_filters(
        request=request,
        start_time=_now() - timedelta(days=7),
        end_time=_now(),
        db=db,
    )

    assert len(response.deployments) == 2
    assert response.models == ["llama3", "mixtral"]
    assert response.ip_addresses == ["203.0.113.10", "203.0.113.11"]
    assert response.status_options == ["all", "success", "error"]
