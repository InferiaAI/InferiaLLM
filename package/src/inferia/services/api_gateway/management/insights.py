from datetime import datetime, timedelta, timezone
from typing import Any, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inferia.services.api_gateway.db.database import get_db
from inferia.services.api_gateway.db.models import (
    Deployment as DBDeployment,
    InferenceLog as DBInferenceLog,
)
from inferia.services.api_gateway.management.dependencies import get_current_user_context
from inferia.services.api_gateway.rbac.authorization import authz_service
from inferia.services.api_gateway.schemas.auth import PermissionEnum
from inferia.services.api_gateway.schemas.insights import (
    InsightsDeploymentFilterOption,
    InsightsFiltersResponse,
    InsightsGranularity,
    InsightsLogsResponse,
    InsightsPagination,
    InsightsStatusFilter,
    InsightsSummaryResponse,
    InsightsThroughput,
    InsightsTimeseriesBucket,
    InsightsTimeseriesResponse,
    InsightsLatency,
    InsightsTotals,
    InsightsTopIp,
    InsightsTopIpsResponse,
    InsightsTopModel,
    InsightsTopModelsResponse,
)

router = APIRouter(prefix="/insights", tags=["Insights"])

MAX_RANGE_DAYS = 90


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_time_window(start_time: datetime, end_time: datetime) -> None:
    if start_time >= end_time:
        raise HTTPException(
            status_code=400,
            detail="Invalid time range: start_time must be before end_time",
        )

    if end_time - start_time > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid time range: maximum allowed range is {MAX_RANGE_DAYS} days",
        )


def _normalize_time_window(
    start_time: datetime, end_time: datetime
) -> tuple[datetime, datetime]:
    normalized_start = _to_utc_naive(start_time)
    normalized_end = _to_utc_naive(end_time)
    _validate_time_window(normalized_start, normalized_end)
    return normalized_start, normalized_end


def _build_filters(
    org_id: str,
    start_time: datetime,
    end_time: datetime,
    deployment_id: str | None,
    model: str | None,
    ip_address: str | None,
    status: InsightsStatusFilter,
) -> List[Any]:
    conditions: List[Any] = [
        DBDeployment.org_id == org_id,
        DBInferenceLog.created_at >= start_time,
        DBInferenceLog.created_at <= end_time,
    ]

    if deployment_id:
        try:
            normalized_deployment_id = UUID(deployment_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid deployment_id"
            ) from exc
        conditions.append(DBInferenceLog.deployment_id == normalized_deployment_id)

    if model:
        conditions.append(DBInferenceLog.model == model)

    if ip_address:
        conditions.append(DBInferenceLog.ip_address == ip_address)

    if status == "success":
        conditions.append(DBInferenceLog.status_code < 400)
    elif status == "error":
        conditions.append(DBInferenceLog.status_code >= 400)

    return conditions


def _to_float(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _to_int(value: Any) -> int:
    return int(value) if value is not None else 0


def _latency_expr():
    # Treat TTFT as latency for insights; fallback keeps non-streaming rows meaningful.
    return func.coalesce(DBInferenceLog.ttft_ms, DBInferenceLog.latency_ms)


def _active_duration_expr():
    # Throughput uses full request duration when available.
    return func.coalesce(DBInferenceLog.latency_ms, DBInferenceLog.ttft_ms)


@router.get("/summary", response_model=InsightsSummaryResponse)
async def get_insights_summary(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    deployment_id: str | None = Query(None),
    model: str | None = Query(None),
    ip_address: str | None = Query(None),
    status: InsightsStatusFilter = Query("all"),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)
    filters = _build_filters(
        user_ctx.org_id,
        normalized_start,
        normalized_end,
        deployment_id,
        model,
        ip_address,
        status,
    )

    success_condition = DBInferenceLog.status_code < 400
    failed_condition = DBInferenceLog.status_code >= 400

    summary_stmt = (
        select(
            func.count(DBInferenceLog.id).label("requests"),
            func.count(DBInferenceLog.id)
            .filter(success_condition)
            .label("successful_requests"),
            func.count(DBInferenceLog.id)
            .filter(failed_condition)
            .label("failed_requests"),
            func.coalesce(func.sum(DBInferenceLog.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            func.coalesce(func.sum(DBInferenceLog.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(DBInferenceLog.total_tokens), 0).label(
                "total_tokens"
            ),
            func.avg(_latency_expr())
            .filter(_latency_expr().isnot(None))
            .label("avg_latency_ms"),
            func.coalesce(func.sum(_active_duration_expr()), 0).label(
                "active_duration_ms"
            ),
            func.avg(DBInferenceLog.tokens_per_second)
            .filter(DBInferenceLog.tokens_per_second.isnot(None))
            .label("avg_tokens_per_second"),
        )
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*filters)
    )

    summary_result = await db.execute(summary_stmt)
    summary = summary_result.first()

    requests = _to_int(summary.requests)
    successful_requests = _to_int(summary.successful_requests)
    failed_requests = _to_int(summary.failed_requests)
    prompt_tokens = _to_int(summary.prompt_tokens)
    completion_tokens = _to_int(summary.completion_tokens)
    total_tokens = _to_int(summary.total_tokens)
    avg_latency = _to_float(summary.avg_latency_ms)
    active_duration_ms = _to_float(getattr(summary, "active_duration_ms", 0.0))
    avg_tokens_per_second = _to_float(getattr(summary, "avg_tokens_per_second", 0.0))

    success_rate = (successful_requests / requests * 100.0) if requests > 0 else 0.0
    active_duration_seconds = active_duration_ms / 1000.0
    if active_duration_seconds > 0:
        requests_per_minute = requests / (active_duration_seconds / 60.0)
        tokens_per_second = completion_tokens / active_duration_seconds
    else:
        requests_per_minute = 0.0
        tokens_per_second = 0.0

    return InsightsSummaryResponse(
        totals=InsightsTotals(
            requests=requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            success_rate=success_rate,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
        latency_ms=InsightsLatency(avg=avg_latency),
        throughput=InsightsThroughput(
            requests_per_minute=requests_per_minute,
            tokens_per_second=tokens_per_second,
            avg_tokens_per_second=avg_tokens_per_second,
        ),
    )


@router.get("/timeseries", response_model=InsightsTimeseriesResponse)
async def get_insights_timeseries(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    deployment_id: str | None = Query(None),
    model: str | None = Query(None),
    ip_address: str | None = Query(None),
    status: InsightsStatusFilter = Query("all"),
    granularity: InsightsGranularity = Query("day"),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)
    filters = _build_filters(
        user_ctx.org_id,
        normalized_start,
        normalized_end,
        deployment_id,
        model,
        ip_address,
        status,
    )

    success_condition = DBInferenceLog.status_code < 400
    failed_condition = DBInferenceLog.status_code >= 400
    bucket_start = func.date_trunc(granularity, DBInferenceLog.created_at).label(
        "bucket_start"
    )

    timeseries_stmt = (
        select(
            bucket_start,
            func.count(DBInferenceLog.id).label("requests"),
            func.count(DBInferenceLog.id)
            .filter(failed_condition)
            .label("failed_requests"),
            func.coalesce(func.sum(DBInferenceLog.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            func.coalesce(func.sum(DBInferenceLog.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(DBInferenceLog.total_tokens), 0).label(
                "total_tokens"
            ),
            func.avg(_latency_expr())
            .filter(_latency_expr().isnot(None))
            .label("avg_latency_ms"),
            func.count(DBInferenceLog.id)
            .filter(success_condition)
            .label("successful_requests"),
        )
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*filters)
        .group_by(bucket_start)
        .order_by(bucket_start.asc())
    )

    timeseries_result = await db.execute(timeseries_stmt)
    rows = timeseries_result.all()

    buckets: List[InsightsTimeseriesBucket] = []
    for row in rows:
        row_requests = _to_int(row.requests)
        row_failed = _to_int(row.failed_requests)
        row_success = _to_int(row.successful_requests)
        row_success_rate = (
            (row_success / row_requests * 100.0) if row_requests > 0 else 0.0
        )
        buckets.append(
            InsightsTimeseriesBucket(
                bucket_start=row.bucket_start,
                requests=row_requests,
                failed_requests=row_failed,
                success_rate=row_success_rate,
                prompt_tokens=_to_int(row.prompt_tokens),
                completion_tokens=_to_int(row.completion_tokens),
                total_tokens=_to_int(row.total_tokens),
                avg_latency_ms=_to_float(row.avg_latency_ms),
            )
        )

    return InsightsTimeseriesResponse(granularity=granularity, buckets=buckets)


@router.get("/top-ips", response_model=InsightsTopIpsResponse)
async def get_insights_top_ips(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    deployment_id: str | None = Query(None),
    model: str | None = Query(None),
    ip_address: str | None = Query(None),
    status: InsightsStatusFilter = Query("all"),
    limit: int = Query(12, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)
    filters = _build_filters(
        user_ctx.org_id,
        normalized_start,
        normalized_end,
        deployment_id,
        model,
        ip_address,
        status,
    )

    success_condition = DBInferenceLog.status_code < 400

    # Ensure we filter out empty IPs
    ip_filters = list(filters)
    ip_filters.append(DBInferenceLog.ip_address.isnot(None))
    ip_filters.append(DBInferenceLog.ip_address != "")

    top_ips_stmt = (
        select(
            DBInferenceLog.ip_address,
            func.count(DBInferenceLog.id).label("requests"),
            func.count(DBInferenceLog.id)
            .filter(success_condition)
            .label("successful_requests"),
            func.coalesce(func.sum(DBInferenceLog.total_tokens), 0).label(
                "total_tokens"
            ),
        )
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*ip_filters)
        .group_by(DBInferenceLog.ip_address)
        .order_by(func.count(DBInferenceLog.id).desc())
        .limit(limit)
    )

    result = await db.execute(top_ips_stmt)
    rows = result.all()

    items = []
    for row in rows:
        requests = _to_int(row.requests)
        successful_requests = _to_int(row.successful_requests)
        success_rate = (successful_requests / requests * 100.0) if requests > 0 else 0.0

        items.append(
            InsightsTopIp(
                ip_address=row.ip_address,
                requests=requests,
                success_rate=success_rate,
                total_tokens=_to_int(row.total_tokens),
            )
        )

    return InsightsTopIpsResponse(items=items)


@router.get("/top-models", response_model=InsightsTopModelsResponse)
async def get_insights_top_models(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    deployment_id: str | None = Query(None),
    model: str | None = Query(None),
    ip_address: str | None = Query(None),
    status: InsightsStatusFilter = Query("all"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)
    filters = _build_filters(
        user_ctx.org_id,
        normalized_start,
        normalized_end,
        deployment_id,
        model,
        ip_address,
        status,
    )

    success_condition = DBInferenceLog.status_code < 400

    top_models_stmt = (
        select(
            DBInferenceLog.model,
            func.count(DBInferenceLog.id).label("requests"),
            func.count(DBInferenceLog.id)
            .filter(success_condition)
            .label("successful_requests"),
            func.coalesce(func.sum(DBInferenceLog.total_tokens), 0).label(
                "total_tokens"
            ),
        )
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*filters)
        .group_by(DBInferenceLog.model)
        .order_by(func.count(DBInferenceLog.id).desc())
        .limit(limit)
    )

    result = await db.execute(top_models_stmt)
    rows = result.all()

    items = []
    for row in rows:
        requests = _to_int(row.requests)
        successful_requests = _to_int(row.successful_requests)
        success_rate = (successful_requests / requests * 100.0) if requests > 0 else 0.0

        items.append(
            InsightsTopModel(
                model=row.model,
                requests=requests,
                success_rate=success_rate,
                total_tokens=_to_int(row.total_tokens),
            )
        )

    return InsightsTopModelsResponse(items=items)


@router.get("/logs", response_model=InsightsLogsResponse)
async def get_insights_logs(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    deployment_id: str | None = Query(None),
    model: str | None = Query(None),
    ip_address: str | None = Query(None),
    status: InsightsStatusFilter = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)
    filters = _build_filters(
        user_ctx.org_id,
        normalized_start,
        normalized_end,
        deployment_id,
        model,
        ip_address,
        status,
    )

    clamped_limit = min(max(limit, 1), 200)

    count_stmt = (
        select(func.count(DBInferenceLog.id))
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*filters)
    )
    total_result = await db.execute(count_stmt)
    total = _to_int(total_result.scalar())

    logs_stmt = (
        select(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(*filters)
        .order_by(DBInferenceLog.created_at.desc())
        .limit(clamped_limit)
        .offset(offset)
    )
    logs_result = await db.execute(logs_stmt)
    logs = logs_result.scalars().all()

    return InsightsLogsResponse(
        items=logs,
        pagination=InsightsPagination(limit=clamped_limit, offset=offset, total=total),
    )


@router.get("/filters", response_model=InsightsFiltersResponse)
async def get_insights_filters(
    request: Request,
    start_time: datetime = Query(..., description="Start datetime (ISO-8601)"),
    end_time: datetime = Query(..., description="End datetime (ISO-8601)"),
    db: AsyncSession = Depends(get_db),
):
    user_ctx = get_current_user_context(request)
    authz_service.require_permission(user_ctx, PermissionEnum.DEPLOYMENT_LIST)

    if not user_ctx.org_id:
        raise HTTPException(
            status_code=400, detail="Action requires organization context"
        )

    normalized_start, normalized_end = _normalize_time_window(start_time, end_time)

    deployments_stmt = (
        select(DBDeployment.id, DBDeployment.model_name)
        .where(DBDeployment.org_id == user_ctx.org_id)
        .order_by(DBDeployment.model_name.asc())
    )
    deployments_result = await db.execute(deployments_stmt)
    deployments = [
        InsightsDeploymentFilterOption(id=str(row.id), model_name=row.model_name)
        for row in deployments_result.all()
    ]

    models_stmt = (
        select(DBInferenceLog.model)
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(
            DBDeployment.org_id == user_ctx.org_id,
            DBInferenceLog.created_at >= normalized_start,
            DBInferenceLog.created_at <= normalized_end,
        )
        .distinct()
        .order_by(DBInferenceLog.model.asc())
    )
    models_result = await db.execute(models_stmt)
    models = [row.model for row in models_result.all() if row.model]

    ip_addresses_stmt = (
        select(DBInferenceLog.ip_address)
        .select_from(DBInferenceLog)
        .join(DBDeployment, DBInferenceLog.deployment_id == DBDeployment.id)
        .where(
            DBDeployment.org_id == user_ctx.org_id,
            DBInferenceLog.created_at >= normalized_start,
            DBInferenceLog.created_at <= normalized_end,
            DBInferenceLog.ip_address.isnot(None),
            DBInferenceLog.ip_address != "",
        )
        .distinct()
        .order_by(DBInferenceLog.ip_address.asc())
    )
    ip_addresses_result = await db.execute(ip_addresses_stmt)
    ip_addresses = [
        row.ip_address for row in ip_addresses_result.all() if row.ip_address
    ]

    return InsightsFiltersResponse(
        deployments=deployments,
        models=models,
        ip_addresses=ip_addresses,
        status_options=["all", "success", "error"],
    )
