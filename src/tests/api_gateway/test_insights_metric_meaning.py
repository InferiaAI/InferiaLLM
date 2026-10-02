"""Each Insights metric must measure what its name claims.

`latency_ms.avg` was `coalesce(ttft_ms, latency_ms)`: time to first token for a
streaming request, total duration for everything else, averaged together. TTFT
is a fraction of a request's duration, so the number moved with the streaming
share of traffic rather than with performance. Adding streaming traffic made
latency appear to improve.

The throughput figures had the inverse coalesce, so they were contaminated the
other way.

These assert on the SQL expressions rather than the handler, because the
existing tests mock the database result entirely and so never exercise the
layer the bug lived in.
"""
from types import SimpleNamespace

import pytest

from api_gateway.management.insights import (
    _active_duration_expr,
    _latency_expr,
    _ttft_expr,
)


class TestExpressionsMeanWhatTheySay:

    def test_latency_is_latency(self):
        assert str(_latency_expr()) == "InferenceLog.latency_ms"

    def test_ttft_is_ttft(self):
        assert str(_ttft_expr()) == "InferenceLog.ttft_ms"

    def test_active_duration_is_latency(self):
        """Throughput divides by summed request duration, not by TTFT."""
        assert str(_active_duration_expr()) == "InferenceLog.latency_ms"

    @pytest.mark.parametrize(
        "expr", [_latency_expr, _ttft_expr, _active_duration_expr]
    )
    def test_nothing_falls_back_to_the_other_column(self, expr):
        sql = str(expr()).lower()
        assert "coalesce" not in sql
        assert not ("latency_ms" in sql and "ttft_ms" in sql)


@pytest.mark.asyncio
class TestTheResponseCarriesBoth:
    """TTFT is reported separately rather than hidden inside latency."""

    @staticmethod
    async def _summary(avg_latency_ms, avg_ttft_ms):
        from datetime import timedelta
        from unittest.mock import AsyncMock, MagicMock

        from tests.api_gateway.test_management_insights import _make_request, _now
        from api_gateway.management.insights import get_insights_summary

        db = AsyncMock()
        result = MagicMock()
        result.first.return_value = SimpleNamespace(
            requests=2,
            successful_requests=2,
            failed_requests=0,
            prompt_tokens=20,
            completion_tokens=40,
            total_tokens=60,
            avg_latency_ms=avg_latency_ms,
            avg_ttft_ms=avg_ttft_ms,
            active_duration_ms=2000.0,
            avg_tokens_per_second=20.0,
        )
        db.execute.return_value = result

        return await get_insights_summary(
            request=_make_request(),
            start_time=_now() - timedelta(hours=2),
            end_time=_now(),
            deployment_id=None,
            model=None,
            status="all",
            db=db,
        )

    async def test_both_are_reported(self):
        got = await self._summary(avg_latency_ms=1400.0, avg_ttft_ms=120.0)
        assert got.latency_ms.avg == 1400.0
        assert got.ttft_ms.avg == 120.0

    async def test_they_are_not_the_same_number(self):
        got = await self._summary(avg_latency_ms=1400.0, avg_ttft_ms=120.0)
        assert got.latency_ms.avg != got.ttft_ms.avg

    async def test_no_streaming_traffic_leaves_ttft_at_zero(self):
        """It must not borrow latency to look populated."""
        got = await self._summary(avg_latency_ms=1400.0, avg_ttft_ms=None)
        assert got.ttft_ms.avg == 0.0
        assert got.latency_ms.avg == 1400.0


def test_the_timeseries_bucket_carries_ttft():
    from api_gateway.schemas.insights import InsightsTimeseriesBucket

    fields = InsightsTimeseriesBucket.model_fields
    assert "avg_latency_ms" in fields
    assert "avg_ttft_ms" in fields


class TestEmptyBucketsReportNothingNotZero:
    """A bucket with no streaming traffic has no TTFT, which is not 0 ms."""

    def test_the_field_is_nullable(self):
        from api_gateway.schemas.insights import InsightsTimeseriesBucket

        bucket = InsightsTimeseriesBucket(bucket_start="2026-10-02T00:00:00")
        assert bucket.avg_ttft_ms is None

    def test_latency_is_not_nullable(self):
        """Every returned bucket has at least one request, so it always has one."""
        from api_gateway.schemas.insights import InsightsTimeseriesBucket

        bucket = InsightsTimeseriesBucket(bucket_start="2026-10-02T00:00:00")
        assert bucket.avg_latency_ms == 0.0

    def test_a_real_value_still_arrives_as_a_number(self):
        from api_gateway.schemas.insights import InsightsTimeseriesBucket

        bucket = InsightsTimeseriesBucket(
            bucket_start="2026-10-02T00:00:00", avg_ttft_ms=120.5
        )
        assert bucket.avg_ttft_ms == 120.5


@pytest.mark.asyncio
async def test_the_summary_statement_compiles_to_the_intended_sql():
    """The expression tests check the pieces; this compiles the real statement."""
    from datetime import timedelta
    from unittest.mock import AsyncMock, MagicMock

    from sqlalchemy.dialects import postgresql

    from api_gateway.management.insights import get_insights_summary
    from tests.api_gateway.test_management_insights import _make_request, _now

    captured = []

    def _execute(stmt, *_a, **_k):
        captured.append(stmt)
        result = MagicMock()
        result.first.return_value = SimpleNamespace(
            requests=1, successful_requests=1, failed_requests=0,
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            avg_latency_ms=10.0, avg_ttft_ms=2.0,
            latency_samples=1, ttft_samples=1,
            active_duration_ms=10.0, avg_tokens_per_second=1.0,
        )
        return result

    db = AsyncMock()
    db.execute.side_effect = _execute

    await get_insights_summary(
        request=_make_request(),
        start_time=_now() - timedelta(hours=1),
        end_time=_now(),
        deployment_id=None,
        model=None,
        status="all",
        db=db,
    )

    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    select = sql.split("FROM")[0]

    assert "avg(inference_logs.latency_ms)" in select
    assert "avg(inference_logs.ttft_ms)" in select
    assert "count(inference_logs.latency_ms) AS latency_samples" in select
    assert "count(inference_logs.ttft_ms) AS ttft_samples" in select

    # The bug: either column standing in for the other.
    assert "coalesce(inference_logs.ttft_ms, inference_logs.latency_ms)" not in select
    assert "coalesce(inference_logs.latency_ms, inference_logs.ttft_ms)" not in select
