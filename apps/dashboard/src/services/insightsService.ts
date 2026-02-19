import api from "@/lib/api";

export type InsightsStatus = "all" | "success" | "error";
export type InsightsGranularity = "hour" | "day";
export type InsightsDeploymentType = "all" | "inference" | "embedding";

export interface InsightsQueryParams {
    start_time: string;
    end_time: string;
    deployment_id?: string;
    model?: string;
    ip_address?: string;
    status?: InsightsStatus;
    deployment_type?: InsightsDeploymentType;
}

export interface InsightsTotals {
    requests: number;
    successful_requests: number;
    failed_requests: number;
    success_rate: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
}

export interface InsightsLatency {
    avg: number;
}

export interface InsightsThroughput {
    requests_per_minute: number;
    tokens_per_second: number;
    avg_tokens_per_second: number;
}

export interface InsightsSummaryResponse {
    totals: InsightsTotals;
    latency_ms: InsightsLatency;
    throughput: InsightsThroughput;
}

export interface InsightsTimeseriesBucket {
    bucket_start: string;
    requests: number;
    failed_requests: number;
    success_rate: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    avg_latency_ms: number;
}

export interface InsightsTimeseriesResponse {
    granularity: InsightsGranularity;
    buckets: InsightsTimeseriesBucket[];
}

export interface InferenceLogItem {
    id: string;
    deployment_id: string;
    user_id: string;
    model: string;
    ip_address: string | null;
    request_payload: Record<string, unknown> | null;
    latency_ms: number | null;
    ttft_ms: number | null;
    tokens_per_second: number | null;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    status_code: number;
    error_message: string | null;
    is_streaming: boolean;
    applied_policies: string[] | null;
    created_at: string;
}

export interface InsightsLogsPagination {
    limit: number;
    offset: number;
    total: number;
}

export interface InsightsLogsResponse {
    items: InferenceLogItem[];
    pagination: InsightsLogsPagination;
}

export interface InsightsDeploymentFilterOption {
    id: string;
    model_name: string;
    model_type?: string;
}

export interface InsightsFiltersResponse {
    deployments: InsightsDeploymentFilterOption[];
    models: string[];
    ip_addresses: string[];
    status_options: InsightsStatus[];
    deployment_types: InsightsDeploymentType[];
}

export interface InsightsTopIp {
    ip_address: string;
    requests: number;
    success_rate: number;
    total_tokens: number;
}

export interface InsightsTopIpsResponse {
    items: InsightsTopIp[];
}

export interface InsightsTopModel {
    model: string;
    requests: number;
    success_rate: number;
    total_tokens: number;
}

export interface InsightsTopModelsResponse {
    items: InsightsTopModel[];
}

export interface InsightsLogsQueryParams extends InsightsQueryParams {
    limit?: number;
    offset?: number;
}

export interface InsightsTimeseriesQueryParams extends InsightsQueryParams {
    granularity: InsightsGranularity;
}

export const insightsService = {
    async getSummary(params: InsightsQueryParams): Promise<InsightsSummaryResponse> {
        const { data } = await api.get<InsightsSummaryResponse>("/management/insights/summary", { params });
        return data;
    },

    async getTimeseries(params: InsightsTimeseriesQueryParams): Promise<InsightsTimeseriesResponse> {
        const { data } = await api.get<InsightsTimeseriesResponse>("/management/insights/timeseries", { params });
        return data;
    },

    async getLogs(params: InsightsLogsQueryParams): Promise<InsightsLogsResponse> {
        const { data } = await api.get<InsightsLogsResponse>("/management/insights/logs", { params });
        return data;
    },

    async getFilters(params: Pick<InsightsQueryParams, "start_time" | "end_time" | "deployment_type">): Promise<InsightsFiltersResponse> {
        const { data } = await api.get<InsightsFiltersResponse>("/management/insights/filters", { params });
        return data;
    },

    async getTopIps(params: InsightsQueryParams): Promise<InsightsTopIpsResponse> {
        const { data } = await api.get<InsightsTopIpsResponse>("/management/insights/top-ips", { params });
        return data;
    },

    async getTopModels(params: InsightsQueryParams): Promise<InsightsTopModelsResponse> {
        const { data } = await api.get<InsightsTopModelsResponse>("/management/insights/top-models", { params });
        return data;
    },
};
