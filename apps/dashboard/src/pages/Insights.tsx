import { type ComponentType, type ReactNode, useCallback, useMemo, useReducer } from "react";
import { useQuery } from "@tanstack/react-query";
import {
    Activity,
    Clock3,
    Download,
    Gauge,
    Layers3,
    RotateCcw,
    TrendingUp,
    TriangleAlert,
    Zap,
    MessageSquare,
    VectorSquare,
} from "lucide-react";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { Suspense, lazy } from "react";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";

const RequestVolumeChart = lazy(() => import("@/components/insights/InsightsCharts").then(m => ({ default: m.RequestVolumeChart })));
const TokenUsageChart = lazy(() => import("@/components/insights/InsightsCharts").then(m => ({ default: m.TokenUsageChart })));
const ModelDistributionChart = lazy(() => import("@/components/insights/InsightsCharts").then(m => ({ default: m.ModelDistributionChart })));
const TrafficByIpChart = lazy(() => import("@/components/insights/InsightsCharts").then(m => ({ default: m.TrafficByIpChart })));

import { Pagination } from "@/components/ui/Pagination";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import {
    type InsightsGranularity,
    type InsightsQueryParams,
    type InsightsStatus,
    type InsightsDeploymentType,
    insightsService,
} from "@/services/insightsService";

type TimePreset = "30m" | "1h" | "5h" | "24h" | "7d" | "30d" | "custom";

const QUERY_STALE_TIME = 30 * 1000;
const IP_DEBOUNCE_DELAY = 300;

const PIE_COLORS = ["#2563eb", "#db2777", "#16a34a", "#9333ea", "#ea580c", "#0891b2"];

function toLocalDateTimeInputValue(date: Date): string {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
}

function parseLocalDateTimeInputValue(value: string): Date | null {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatNumber(value: number, maxFractionDigits = 2): string {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: maxFractionDigits }).format(value);
}

function formatBucketLabel(value: string, granularity: InsightsGranularity): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    if (granularity === "hour") {
        return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

type State = {
    deploymentType: InsightsDeploymentType;
    timePreset: TimePreset;
    customStart: string;
    customEnd: string;
    deploymentId: string;
    ipAddress: string;
    status: InsightsStatus;
    page: number;
    pageSize: number;
};

type Action =
    | { type: 'SET_FIELD'; field: keyof State; value: any }
    | { type: 'SET_DEPLOYMENT_TYPE'; payload: InsightsDeploymentType }
    | { type: 'CLEAR_FILTERS' };

const createInitialState = (defaultStart: Date, now: Date): State => ({
    deploymentType: "inference",
    timePreset: "7d",
    customStart: toLocalDateTimeInputValue(defaultStart),
    customEnd: toLocalDateTimeInputValue(now),
    deploymentId: "",
    ipAddress: "",
    status: "all",
    page: 1,
    pageSize: 20,
});

function reducer(state: State, action: Action): State {
    switch (action.type) {
        case 'SET_FIELD':
            return { ...state, [action.field]: action.value };
        case 'SET_DEPLOYMENT_TYPE':
            return { ...state, deploymentType: action.payload, deploymentId: "", page: 1 };
        case 'CLEAR_FILTERS':
            return { ...state, deploymentId: "", ipAddress: "", status: "all", page: 1 };
        default:
            return state;
    }
}

export default function Insights() {
    const now = new Date();
    const defaultStart = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    const [state, dispatch] = useReducer(reducer, createInitialState(defaultStart, now));
    const {
        deploymentType, timePreset, customStart, customEnd,
        deploymentId, ipAddress, status, page, pageSize
    } = state;

    const debouncedIpAddress = useDebouncedValue(ipAddress.trim(), IP_DEBOUNCE_DELAY);

    const range = useMemo(() => {
        const end = new Date();
        if (timePreset === "30m") {
            return { start: new Date(end.getTime() - 30 * 60 * 1000), end };
        }
        if (timePreset === "1h") {
            return { start: new Date(end.getTime() - 60 * 60 * 1000), end };
        }
        if (timePreset === "5h") {
            return { start: new Date(end.getTime() - 5 * 60 * 60 * 1000), end };
        }
        if (timePreset === "24h") {
            return { start: new Date(end.getTime() - 24 * 60 * 60 * 1000), end };
        }
        if (timePreset === "7d") {
            return { start: new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000), end };
        }
        if (timePreset === "30d") {
            return { start: new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000), end };
        }

        const parsedStart = parseLocalDateTimeInputValue(customStart);
        const parsedEnd = parseLocalDateTimeInputValue(customEnd);
        if (!parsedStart || !parsedEnd || parsedStart >= parsedEnd) {
            return { start: new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000), end };
        }
        return { start: parsedStart, end: parsedEnd };
    }, [timePreset, customStart, customEnd]);

    const baseParams: InsightsQueryParams = useMemo(
        () => ({
            start_time: range.start.toISOString(),
            end_time: range.end.toISOString(),
            deployment_id: deploymentId || undefined,
            ip_address: debouncedIpAddress || undefined,
            status,
            deployment_type: deploymentType,
        }),
        [range.start, range.end, deploymentId, debouncedIpAddress, status, deploymentType]
    );

    const granularity: InsightsGranularity = useMemo(() => {
        const durationHours = (range.end.getTime() - range.start.getTime()) / (1000 * 60 * 60);
        return durationHours <= 48 ? "hour" : "day";
    }, [range.start, range.end]);

    const filtersQuery = useQuery({
        queryKey: ["insights", "filters", baseParams.start_time, baseParams.end_time, deploymentType],
        queryFn: () =>
            insightsService.getFilters({
                start_time: baseParams.start_time,
                end_time: baseParams.end_time,
                deployment_type: deploymentType,
            }),
        staleTime: QUERY_STALE_TIME,
    });

    const summaryQuery = useQuery({
        queryKey: [
            "insights",
            "summary",
            baseParams.start_time,
            baseParams.end_time,
            deploymentId,
            debouncedIpAddress,
            status,
            deploymentType,
        ],
        queryFn: () => insightsService.getSummary(baseParams),
        staleTime: QUERY_STALE_TIME,
    });

    const timeseriesQuery = useQuery({
        queryKey: [
            "insights",
            "timeseries",
            baseParams.start_time,
            baseParams.end_time,
            deploymentId,
            debouncedIpAddress,
            status,
            granularity,
            deploymentType,
        ],
        queryFn: () => insightsService.getTimeseries({ ...baseParams, granularity }),
        staleTime: QUERY_STALE_TIME,
    });

    const topIpsQuery = useQuery({
        queryKey: [
            "insights",
            "top-ips",
            baseParams.start_time,
            baseParams.end_time,
            deploymentId,
            debouncedIpAddress,
            status,
            deploymentType,
        ],
        queryFn: () => insightsService.getTopIps(baseParams),
        staleTime: QUERY_STALE_TIME,
    });

    const topModelsQuery = useQuery({
        queryKey: [
            "insights",
            "top-models",
            baseParams.start_time,
            baseParams.end_time,
            deploymentId,
            debouncedIpAddress,
            status,
            deploymentType,
        ],
        queryFn: () => insightsService.getTopModels(baseParams),
        staleTime: QUERY_STALE_TIME,
    });

    const logsQuery = useQuery({
        queryKey: [
            "insights",
            "logs",
            baseParams.start_time,
            baseParams.end_time,
            deploymentId,
            debouncedIpAddress,
            status,
            page,
            pageSize,
            deploymentType,
        ],
        queryFn: () =>
            insightsService.getLogs({
                ...baseParams,
                limit: pageSize,
                offset: (page - 1) * pageSize,
            }),
        staleTime: QUERY_STALE_TIME,
    });

    const summary = summaryQuery.data;
    const timeseries = timeseriesQuery.data;
    const logs = logsQuery.data;
    const filters = filtersQuery.data;

    const isEmbedding = deploymentType === "embedding";
    const filteredDeployments = useMemo(() => {
        const deployments = filters?.deployments || [];
        return deployments.filter((d) => {
            if (deploymentType === "all") return true;
            return d.model_type === deploymentType || (!d.model_type && deploymentType === "inference");
        });
    }, [filters?.deployments, deploymentType]);

    const chartData = useMemo(
        () =>
            (timeseries?.buckets || []).map((bucket) => ({
                ...bucket,
                label: formatBucketLabel(bucket.bucket_start, granularity),
                successful_requests: Math.max(bucket.requests - bucket.failed_requests, 0),
            })),
        [timeseries?.buckets, granularity]
    );
    const requestQualityData = useMemo(() => chartData.slice(-12), [chartData]);
    const chartTotals = useMemo(() => {
        const totalBuckets = chartData.length || 1;
        const totalRequests = chartData.reduce((sum, item) => sum + item.requests, 0);
        const totalSuccess = chartData.reduce((sum, item) => sum + item.successful_requests, 0);
        const totalFailed = chartData.reduce((sum, item) => sum + item.failed_requests, 0);
        const totalPrompt = chartData.reduce((sum, item) => sum + item.prompt_tokens, 0);
        const totalCompletion = chartData.reduce((sum, item) => sum + item.completion_tokens, 0);
        const avgReqPerBucket = totalRequests / totalBuckets;
        const avgLatencyAcrossBuckets =
            chartData.reduce((sum, item) => sum + item.avg_latency_ms, 0) / totalBuckets;

        const peakRequestsBucket = chartData.reduce(
            (acc, item) => (item.requests > acc.requests ? item : acc),
            chartData[0] || { label: "-", requests: 0 }
        );
        const slowestLatencyBucket = chartData.reduce(
            (acc, item) => (item.avg_latency_ms > acc.avg_latency_ms ? item : acc),
            chartData[0] || { label: "-", avg_latency_ms: 0 }
        );
        const fastestLatencyBucket = chartData.reduce(
            (acc, item) => (item.avg_latency_ms < acc.avg_latency_ms ? item : acc),
            chartData[0] || { label: "-", avg_latency_ms: 0 }
        );

        return {
            totalBuckets,
            totalRequests,
            totalSuccess,
            totalFailed,
            totalPrompt,
            totalCompletion,
            avgReqPerBucket,
            avgLatencyAcrossBuckets,
            peakRequestsBucket,
            slowestLatencyBucket,
            fastestLatencyBucket,
        };
    }, [chartData]);

    const totalItems = logs?.pagination.total ?? 0;
    const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));

    const isInitialLoading =
        summaryQuery.isLoading ||
        timeseriesQuery.isLoading ||
        topIpsQuery.isLoading ||
        topModelsQuery.isLoading ||
        logsQuery.isLoading;
    const hasNoData = (summary?.totals.requests ?? 0) === 0 && !isInitialLoading;

    const hasActiveFilters = deploymentId !== "" || ipAddress !== "" || status !== "all";

    const clearFilters = useCallback(() => {
        dispatch({ type: 'CLEAR_FILTERS' });
    }, []);

    const exportLogsToCSV = useCallback(() => {
        if (!logs?.items.length) return;
        const headers = ["Timestamp", "Deployment ID", "Model", "IP Address", "Prompt Tokens", "Completion Tokens", "Total Tokens", "Latency (ms)", "Status"];
        const rows = logs.items.map((log) => [
            log.created_at,
            log.deployment_id,
            log.model,
            log.ip_address || "",
            log.prompt_tokens,
            log.completion_tokens,
            log.total_tokens,
            log.ttft_ms ?? log.latency_ms ?? "",
            log.status_code,
        ]);
        const csvContent = [headers, ...rows].map((row) => row.join(",")).join("\n");
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `insights-logs-${new Date().toISOString().split("T")[0]}.csv`;
        link.click();
        URL.revokeObjectURL(link.href);
    }, [logs]);

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-1">
                <h1 className="text-2xl font-bold tracking-tight">Insights</h1>
                <p className="text-muted-foreground">
                    Analytics from {isEmbedding ? "embedding" : "inference"} logs across your organization.
                </p>
            </div>

            <div className="flex border-b">
                <button
                    type="button"
                    onClick={() => {
                        dispatch({ type: 'SET_DEPLOYMENT_TYPE', payload: "inference" });
                    }}
                    className={cn(
                        "flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors",
                        deploymentType === "inference"
                            ? "border-primary text-primary"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                >
                    <MessageSquare className="h-4 w-4" />
                    Inference
                </button>
                <button
                    type="button"
                    onClick={() => {
                        dispatch({ type: 'SET_DEPLOYMENT_TYPE', payload: "embedding" });
                    }}
                    className={cn(
                        "flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors",
                        deploymentType === "embedding"
                            ? "border-primary text-primary"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                >
                    <VectorSquare className="h-4 w-4" />
                    Embeddings
                </button>
            </div>

            <div className="rounded-xl border bg-card p-4">
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-6">
                    <div className="space-y-1">
                        <label htmlFor="insight-time-preset" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Time range</label>
                        <select
                            id="insight-time-preset"
                            value={timePreset}
                            onChange={(e) => {
                                dispatch({ type: 'SET_FIELD', field: 'timePreset', value: e.target.value as TimePreset });
                                dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                            }}
                            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                        >
                            <option value="30m">Last 30 mins</option>
                            <option value="1h">Last 1 hour</option>
                            <option value="5h">Last 5 hours</option>
                            <option value="24h">Last 24 hours</option>
                            <option value="7d">Last 7 days</option>
                            <option value="30d">Last 30 days</option>
                            <option value="custom">Custom</option>
                        </select>
                    </div>

                    {timePreset === "custom" && (
                        <>
                            <div className="space-y-1">
                                <label htmlFor="insight-custom-start" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Start</label>
                                <input
                                    id="insight-custom-start"
                                    type="datetime-local"
                                    value={customStart}
                                    onChange={(e) => {
                                        dispatch({ type: 'SET_FIELD', field: 'customStart', value: e.target.value });
                                        dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                                    }}
                                    className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                                />
                            </div>
                            <div className="space-y-1">
                                <label htmlFor="insight-custom-end" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">End</label>
                                <input
                                    id="insight-custom-end"
                                    type="datetime-local"
                                    value={customEnd}
                                    onChange={(e) => {
                                        dispatch({ type: 'SET_FIELD', field: 'customEnd', value: e.target.value });
                                        dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                                    }}
                                    className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                                />
                            </div>
                        </>
                    )}

                    <div className="space-y-1">
                        <label htmlFor="insight-deployment-id" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Deployment</label>
                        <select
                            id="insight-deployment-id"
                            value={deploymentId}
                            onChange={(e) => {
                                dispatch({ type: 'SET_FIELD', field: 'deploymentId', value: e.target.value });
                                dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                            }}
                            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                        >
                            <option value="">All {isEmbedding ? "embedding" : "inference"} deployments</option>
                            {filteredDeployments.map((deployment) => (
                                <option key={deployment.id} value={deployment.id}>
                                    {deployment.model_name} ({(deployment.id || "").slice(0, 8)})
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="space-y-1">
                        <label htmlFor="insight-status" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Status</label>
                        <select
                            id="insight-status"
                            value={status}
                            onChange={(e) => {
                                dispatch({ type: 'SET_FIELD', field: 'status', value: e.target.value as InsightsStatus });
                                dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                            }}
                            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                        >
                            {(filters?.status_options || ["all", "success", "error"]).map((option) => (
                                <option key={option} value={option}>
                                    {option[0].toUpperCase() + option.slice(1)}
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="space-y-1">
                        <label htmlFor="insight-ip-address" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">IP Address</label>
                        <input
                            id="insight-ip-address"
                            type="text"
                            list="insights-ip-options"
                            value={ipAddress}
                            onChange={(e) => {
                                dispatch({ type: 'SET_FIELD', field: 'ipAddress', value: e.target.value });
                                dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                            }}
                            placeholder="All IPs"
                            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                        />
                        <datalist id="insights-ip-options">
                            {(filters?.ip_addresses || []).map((ipOption) => (
                                <option key={ipOption} value={ipOption} />
                            ))}
                        </datalist>
                    </div>
                </div>
                {hasActiveFilters && (
                    <div className="mt-3 flex justify-end">
                        <button
                            type="button"
                            onClick={clearFilters}
                            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                        >
                            <RotateCcw className="h-3.5 w-3.5" />
                            Clear all filters
                        </button>
                    </div>
                )}
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
                <MetricCard
                    icon={Activity}
                    title="Requests"
                    value={isInitialLoading ? "..." : formatNumber(summary?.totals.requests || 0, 0)}
                    subtitle={`${formatNumber(summary?.totals.successful_requests || 0, 0)} successful`}
                />
                <MetricCard
                    icon={TrendingUp}
                    title="Success Rate"
                    value={isInitialLoading ? "..." : `${formatNumber(summary?.totals.success_rate || 0)}%`}
                    subtitle={`${formatNumber(summary?.totals.failed_requests || 0, 0)} failed`}
                />
                <MetricCard
                    icon={Layers3}
                    title={isEmbedding ? "Total Tokens" : "Total Tokens"}
                    value={isInitialLoading ? "..." : formatNumber(summary?.totals.total_tokens || 0, 0)}
                    subtitle={isEmbedding
                        ? `${formatNumber(summary?.totals.prompt_tokens || 0, 0)} tokens processed`
                        : `In ${formatNumber(summary?.totals.prompt_tokens || 0, 0)} / Out ${formatNumber(summary?.totals.completion_tokens || 0, 0)}`
                    }
                />
                <MetricCard
                    icon={Gauge}
                    title={isEmbedding ? "Avg Latency" : "Avg Latency (TTFT)"}
                    value={isInitialLoading ? "..." : `${formatNumber(summary?.latency_ms.avg || 0)} ms`}
                    subtitle={`${formatNumber(summary?.throughput.requests_per_minute || 0)} req/min (active time)`}
                />
                <MetricCard
                    icon={Zap}
                    title={isEmbedding ? "Avg Req/min" : "Avg Token/s"}
                    value={isInitialLoading ? "..." : isEmbedding
                        ? formatNumber(summary?.throughput.requests_per_minute || 0, 0)
                        : formatNumber(summary?.throughput.avg_tokens_per_second || 0)
                    }
                    subtitle={isEmbedding
                        ? `${formatNumber(summary?.totals.requests || 0, 0)} total requests`
                        : `${formatNumber(summary?.throughput.tokens_per_second || 0)} tok/s overall`
                    }
                />
            </div>

            {isInitialLoading && (
                <div className="grid gap-4 lg:grid-cols-2">
                    <Skeleton className="h-[320px] w-full" />
                    <Skeleton className="h-[320px] w-full" />
                </div>
            )}

            {!isInitialLoading && hasNoData && (
                <EmptyState
                    icon={TriangleAlert}
                    title={`No ${isEmbedding ? "embedding" : "inference"} data in selected range`}
                    description="Try expanding the date range or removing one or more filters."
                />
            )}

            {!isInitialLoading && !hasNoData && (
                <>
                    <div className="grid gap-4 lg:grid-cols-2">
                        <ChartCard title="Request Quality" subtitle="Successful vs failed requests per bucket">
                            <ChartLegend
                                items={[
                                    { label: "Successful", colorClass: "bg-green-600" },
                                    { label: "Failed", colorClass: "bg-red-500" },
                                ]}
                            />
                            <RequestQualityGraph data={requestQualityData} />
                            <ChartSummary
                                items={[
                                    `Successful: ${formatNumber(chartTotals.totalSuccess, 0)}`,
                                    `Failed: ${formatNumber(chartTotals.totalFailed, 0)}`,
                                    `Failure rate: ${formatNumber(
                                        chartTotals.totalRequests > 0
                                            ? (chartTotals.totalFailed / chartTotals.totalRequests) * 100
                                            : 0
                                    )}%`,
                                ]}
                            />
                        </ChartCard>

                        <ChartCard title="Request Volume" subtitle={`${granularity === "hour" ? "Hourly" : "Daily"} requests`}>
                            <ChartLegend items={[{ label: "Requests", colorClass: "bg-emerald-600" }]} />
                            <Suspense fallback={<Skeleton className="h-[260px] w-full" />}>
                                <RequestVolumeChart data={chartData} />
                            </Suspense>
                            <ChartSummary
                                items={[
                                    `Total: ${formatNumber(chartTotals.totalRequests, 0)}`,
                                    `Avg per bucket: ${formatNumber(chartTotals.avgReqPerBucket, 1)}`,
                                    `Peak: ${peakBucketText(
                                        chartTotals.peakRequestsBucket.label,
                                        chartTotals.peakRequestsBucket.requests
                                    )}`,
                                ]}
                            />
                        </ChartCard>

                        <ChartCard
                            title={isEmbedding ? "Tokens Processed" : "Token Usage"}
                            subtitle={isEmbedding ? "Total tokens processed over time" : "Prompt vs completion tokens"}
                        >
                            <Suspense fallback={<Skeleton className="h-[260px] w-full" />}>
                                <TokenUsageChart data={chartData} isEmbedding={isEmbedding} />
                            </Suspense>
                        </ChartCard>
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                        <ChartCard title="Requests by Model" subtitle="Distribution of traffic across models">
                            <Suspense fallback={<Skeleton className="h-[260px] w-full" />}>
                                <ModelDistributionChart data={topModelsQuery.data?.items || []} />
                            </Suspense>
                            <ChartSummary
                                items={[
                                    `Models active: ${topModelsQuery.data?.items.length || 0}`,
                                    `Top model: ${topModelsQuery.data?.items[0]?.model || "-"
                                    }`,
                                ]}
                            />
                        </ChartCard>

                        <ChartCard title="Top IPs by Traffic" subtitle="Top active clients by request volume">
                            <ChartLegend items={[{ label: "Requests", colorClass: "bg-purple-600" }]} />
                            <Suspense fallback={<Skeleton className="h-[260px] w-full" />}>
                                <TrafficByIpChart data={topIpsQuery.data?.items || []} />
                            </Suspense>
                            <ChartSummary
                                items={[
                                    `Unique IPs: ${topIpsQuery.data?.items.length || 0}`,
                                    `Top IP: ${topIpsQuery.data?.items[0]
                                        ? `${topIpsQuery.data.items[0].ip_address} (${formatNumber(
                                            topIpsQuery.data.items[0].requests
                                        )})`
                                        : "-"
                                    }`,
                                ]}
                            />
                        </ChartCard>
                    </div>

                    <ChartCard
                        title="Average Latency Trend"
                        subtitle={isEmbedding ? "Average response latency over time" : "Average TTFT over time (fallback: total latency when TTFT missing)"}
                    >
                        <ChartLegend items={[{ label: isEmbedding ? "Avg Latency (ms)" : "Avg Latency (TTFT, ms)", colorClass: "bg-sky-500" }]} />
                        <ResponsiveContainer width="100%" height={300}>
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={20} />
                                <YAxis tick={{ fontSize: 11 }} />
                                <Tooltip />
                                <Legend />
                                <Line
                                    type="monotone"
                                    dataKey="avg_latency_ms"
                                    stroke="#0ea5e9"
                                    strokeWidth={2}
                                    dot={false}
                                    name={isEmbedding ? "Avg Latency (ms)" : "Avg Latency (TTFT, ms)"}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                        <ChartSummary
                            items={[
                                `Avg across buckets: ${formatNumber(chartTotals.avgLatencyAcrossBuckets)} ms`,
                                `Fastest: ${peakBucketText(
                                    chartTotals.fastestLatencyBucket.label,
                                    chartTotals.fastestLatencyBucket.avg_latency_ms,
                                    "ms"
                                )}`,
                                `Slowest: ${peakBucketText(
                                    chartTotals.slowestLatencyBucket.label,
                                    chartTotals.slowestLatencyBucket.avg_latency_ms,
                                    "ms"
                                )}`,
                            ]}
                        />
                    </ChartCard>

                    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
                        <div className="border-b px-4 py-3">
                            <h3 className="font-semibold">Trend Breakdown</h3>
                            <p className="text-xs text-muted-foreground">
                                Exact values per time bucket for quick interpretation.
                            </p>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-muted/40 text-muted-foreground">
                                    <tr>
                                        <th className="px-4 py-3 text-left font-medium">Bucket</th>
                                        <th className="px-4 py-3 text-left font-medium">Requests</th>
                                        <th className="px-4 py-3 text-left font-medium">Success %</th>
                                        <th className="px-4 py-3 text-left font-medium">Avg Latency (TTFT)</th>
                                        <th className="px-4 py-3 text-left font-medium">Total Tokens</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y">
                                    {chartData.slice(-12).map((bucket) => (
                                        <tr key={bucket.bucket_start} className="hover:bg-muted/20">
                                            <td className="px-4 py-3 text-xs font-mono text-muted-foreground">
                                                {bucket.label}
                                            </td>
                                            <td className="px-4 py-3">{formatNumber(bucket.requests, 0)}</td>
                                            <td className="px-4 py-3">{formatNumber(bucket.success_rate)}%</td>
                                            <td className="px-4 py-3">{formatNumber(bucket.avg_latency_ms)} ms</td>
                                            <td className="px-4 py-3">{formatNumber(bucket.total_tokens, 0)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}

            <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
                <div className="border-b px-4 py-3 flex items-start justify-between">
                    <div>
                        <h3 className="font-semibold">Detailed {isEmbedding ? "Embedding" : "Inference"} Logs</h3>
                        <p className="text-xs text-muted-foreground">
                            {isEmbedding
                                ? `${formatNumber(summary?.throughput.requests_per_minute || 0, 0)} req/min avg`
                                : `Avg token speed: ${formatNumber(summary?.throughput.avg_tokens_per_second || 0)} tok/s | Overall token speed: ${formatNumber(summary?.throughput.tokens_per_second || 0)} tok/s`
                            }
                        </p>
                    </div>
                    {logs?.items.length ? (
                        <button
                            type="button"
                            onClick={exportLogsToCSV}
                            className="inline-flex items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted transition-colors"
                        >
                            <Download className="h-3.5 w-3.5" />
                            Export CSV
                        </button>
                    ) : null}
                </div>

                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-muted/40 text-muted-foreground">
                            <tr>
                                <th className="px-4 py-3 text-left font-medium">Timestamp</th>
                                <th className="px-4 py-3 text-left font-medium">Deployment</th>
                                <th className="px-4 py-3 text-left font-medium">Model</th>
                                <th className="px-4 py-3 text-left font-medium">IP</th>
                                <th className="px-4 py-3 text-left font-medium">{isEmbedding ? "Tokens" : "Tokens (In/Out)"}</th>
                                <th className="px-4 py-3 text-left font-medium">{isEmbedding ? "Latency" : "Latency (TTFT)"}</th>
                                <th className="px-4 py-3 text-left font-medium">Status</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y">
                            {(logs?.items || []).map((log) => (
                                <tr key={log.id} className="hover:bg-muted/20">
                                    <td className="px-4 py-3 text-xs font-mono text-muted-foreground">
                                        {new Date(log.created_at).toLocaleString()}
                                    </td>
                                    <td className="px-4 py-3 text-xs font-mono">
                                        {String(log.deployment_id).slice(0, 8)}...
                                    </td>
                                    <td className="px-4 py-3">{log.model}</td>
                                    <td className="px-4 py-3 font-mono text-xs">{log.ip_address || "-"}</td>
                                    <td className="px-4 py-3 font-mono text-xs">
                                        {isEmbedding
                                            ? formatNumber(log.total_tokens, 0)
                                            : `${formatNumber(log.total_tokens, 0)} (${formatNumber(log.prompt_tokens, 0)}/${formatNumber(log.completion_tokens, 0)})`
                                        }
                                    </td>
                                    <td className="px-4 py-3 font-mono text-xs">
                                        {log.ttft_ms !== null && log.ttft_ms !== undefined
                                            ? `${formatNumber(log.ttft_ms)} ms`
                                            : log.latency_ms !== null && log.latency_ms !== undefined
                                                ? `${formatNumber(log.latency_ms)} ms`
                                                : "-"}
                                    </td>
                                    <td className="px-4 py-3">
                                        <span
                                            className={cn(
                                                "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium border",
                                                log.status_code < 400
                                                    ? "bg-green-500/10 text-green-600 border-green-500/30"
                                                    : "bg-red-500/10 text-red-500 border-red-500/30"
                                            )}
                                        >
                                            {log.status_code}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                            {!logsQuery.isLoading && (logs?.items.length || 0) === 0 && (
                                <tr>
                                    <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                                        No logs found for the selected filters.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>

                {(logs?.pagination.total || 0) > 0 && (
                    <Pagination
                        currentPage={page}
                        totalPages={totalPages}
                        onPageChange={(p) => dispatch({ type: 'SET_FIELD', field: 'page', value: p })}
                        pageSize={pageSize}
                        onPageSizeChange={(size) => {
                            dispatch({ type: 'SET_FIELD', field: 'pageSize', value: size });
                            dispatch({ type: 'SET_FIELD', field: 'page', value: 1 });
                        }}
                        totalItems={totalItems}
                    />
                )}
            </div>
        </div>
    );
}

function MetricCard({
    icon: Icon,
    title,
    value,
    subtitle,
}: {
    icon: ComponentType<{ className?: string }>;
    title: string;
    value: string;
    subtitle: string;
}) {
    return (
        <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</span>
                <Icon className="h-4 w-4 text-emerald-500" />
            </div>
            <div className="text-2xl font-bold">{value}</div>
            <div className="mt-1 text-xs text-muted-foreground">{subtitle}</div>
        </div>
    );
}

function ChartCard({
    title,
    subtitle,
    children,
}: {
    title: string;
    subtitle?: string;
    children: ReactNode;
}) {
    return (
        <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-start justify-between">
                <div>
                    <h3 className="font-semibold">{title}</h3>
                    {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
                </div>
                <Clock3 className="h-4 w-4 text-muted-foreground" />
            </div>
            {children}
        </div>
    );
}

function ChartLegend({
    items,
}: {
    items: Array<{ label: string; colorClass: string }>;
}) {
    return (
        <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {items.map((item) => (
                <span key={item.label} className="inline-flex items-center gap-1.5">
                    <span className={cn("h-2.5 w-2.5 rounded-full", item.colorClass)} />
                    {item.label}
                </span>
            ))}
        </div>
    );
}

function ChartSummary({ items }: { items: string[] }) {
    return (
        <div className="mt-2 grid gap-2 border-t pt-2 text-xs text-muted-foreground md:grid-cols-3">
            {items.map((item) => (
                <span key={item}>{item}</span>
            ))}
        </div>
    );
}

function peakBucketText(label: string, value: number, unit = "req") {
    return `${label} (${formatNumber(value)} ${unit})`;
}

function RequestQualityGraph({
    data,
}: {
    data: Array<{
        bucket_start: string;
        label: string;
        successful_requests: number;
        failed_requests: number;
    }>;
}) {
    const maxBucketValue = Math.max(
        1,
        ...data.map((item) => Math.max(item.successful_requests, item.failed_requests))
    );

    return (
        <div className="h-[260px] rounded-md border bg-muted/10 p-3">
            <div className="flex h-full items-end gap-2 overflow-x-auto pb-8">
                {data.map((item) => (
                    <div key={item.bucket_start} className="flex min-w-[72px] flex-col items-center gap-2">
                        <div className="flex h-[170px] w-full items-end justify-center gap-1">
                            <div className="flex h-full w-5 flex-col items-center justify-end">
                                <div
                                    className="w-full rounded-t bg-green-600"
                                    style={{
                                        height: `${Math.max(
                                            (item.successful_requests / maxBucketValue) * 100,
                                            item.successful_requests > 0 ? 3 : 0
                                        )}%`,
                                    }}
                                    title={`Successful: ${item.successful_requests}`}
                                />
                            </div>
                            <div className="flex h-full w-5 flex-col items-center justify-end">
                                <div
                                    className="w-full rounded-t bg-red-500"
                                    style={{
                                        height: `${Math.max(
                                            (item.failed_requests / maxBucketValue) * 100,
                                            item.failed_requests > 0 ? 3 : 0
                                        )}%`,
                                    }}
                                    title={`Failed: ${item.failed_requests}`}
                                />
                            </div>
                        </div>
                        <div className="text-[11px] text-muted-foreground">
                            {item.successful_requests}/{item.failed_requests}
                        </div>
                        <div className="w-full truncate text-center text-[11px] text-muted-foreground">
                            {item.label}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
