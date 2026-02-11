from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from inferia.services.filtration.schemas.logging import InferenceLogResponse

InsightsStatusFilter = Literal["all", "success", "error"]
InsightsGranularity = Literal["hour", "day"]


class InsightsFilterParams(BaseModel):
    start_time: datetime
    end_time: datetime
    deployment_id: Optional[str] = None
    model: Optional[str] = None
    status: InsightsStatusFilter = "all"


class InsightsTotals(BaseModel):
    requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    success_rate: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class InsightsLatency(BaseModel):
    avg: float = 0.0


class InsightsThroughput(BaseModel):
    requests_per_minute: float = 0.0
    tokens_per_second: float = 0.0


class InsightsSummaryResponse(BaseModel):
    totals: InsightsTotals
    latency_ms: InsightsLatency
    throughput: InsightsThroughput


class InsightsTimeseriesBucket(BaseModel):
    bucket_start: datetime
    requests: int = 0
    failed_requests: int = 0
    success_rate: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    avg_latency_ms: float = 0.0


class InsightsTimeseriesResponse(BaseModel):
    granularity: InsightsGranularity
    buckets: List[InsightsTimeseriesBucket] = Field(default_factory=list)


class InsightsPagination(BaseModel):
    limit: int
    offset: int
    total: int


class InsightsLogsResponse(BaseModel):
    items: List[InferenceLogResponse] = Field(default_factory=list)
    pagination: InsightsPagination


class InsightsDeploymentFilterOption(BaseModel):
    id: str
    model_name: str


class InsightsFiltersResponse(BaseModel):
    deployments: List[InsightsDeploymentFilterOption] = Field(default_factory=list)
    models: List[str] = Field(default_factory=list)
    status_options: List[InsightsStatusFilter] = Field(
        default_factory=lambda: ["all", "success", "error"]
    )
