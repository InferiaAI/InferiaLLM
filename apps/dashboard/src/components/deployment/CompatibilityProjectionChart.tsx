import { useMemo } from "react";
import {
    CartesianGrid,
    LabelList,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import {
    DEFAULT_CONCURRENCY_LEVELS,
    projectCompatibilityPerformance,
    type CompatibilityResult,
} from "@/services/gpuCompatibility";

interface CompatibilityProjectionChartProps {
    compatibility: CompatibilityResult;
    poolName?: string;
    inputTokens?: number;
    outputTokens?: number;
}

type ProjectionDatum = {
    concurrency: number;
    ttftSeconds: number;
    referenceTtftSeconds: number;
    totalTps: number;
    tpsPerUser: number;
    tpsPerUserLabel: string;
};

function formatTpsPerUser(value: number): string {
    if (value >= 100) return value.toFixed(0);
    if (value >= 10) return value.toFixed(1);
    return value.toFixed(2);
}

function formatSeconds(value: number): string {
    if (value >= 100) return `${value.toFixed(0)}s`;
    if (value >= 10) return `${value.toFixed(1)}s`;
    return `${value.toFixed(2)}s`;
}

export function CompatibilityProjectionChart({
    compatibility,
    poolName,
    inputTokens = 200,
    outputTokens = 200,
}: CompatibilityProjectionChartProps) {
    const chartData = useMemo<ProjectionDatum[]>(() => {
        return projectCompatibilityPerformance(compatibility, {
            inputTokens,
            outputTokens,
        }).map((point) => ({
            ...point,
            tpsPerUserLabel: formatTpsPerUser(point.tpsPerUser),
        }));
    }, [compatibility, inputTokens, outputTokens]);

    if (chartData.length === 0) return null;

    const concurrencyTicks = chartData.map((point) => point.concurrency);
    const minConcurrency = concurrencyTicks[0] ?? 1;
    const maxConcurrency = concurrencyTicks[concurrencyTicks.length - 1] ?? 500;

    return (
        <div className="rounded-lg border border-current/15 bg-white/35 dark:bg-black/10 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                    <h4 className="text-xs font-black uppercase tracking-widest opacity-70">
                        TTFT vs Concurrency ({inputTokens}/{outputTokens} ISL/OSL)
                    </h4>
                    <p className="text-[11px] font-medium opacity-70 mt-1">
                        Lower TTFT is better. Labels show projected TPS per user.
                    </p>
                </div>
            </div>

            <div className="h-[290px]">
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                        data={chartData}
                        margin={{ top: 28, right: 14, left: 2, bottom: 6 }}
                    >
                        <CartesianGrid strokeDasharray="4 4" stroke="currentColor" opacity={0.15} />
                        <XAxis
                            type="number"
                            dataKey="concurrency"
                            scale="log"
                            domain={[minConcurrency, maxConcurrency]}
                            ticks={concurrencyTicks}
                            allowDataOverflow
                            tick={{ fontSize: 11, fill: "currentColor", opacity: 0.8 }}
                            tickFormatter={(value) => String(value)}
                            label={{
                                value: "Concurrency (users)",
                                position: "insideBottom",
                                offset: -5,
                                fill: "currentColor",
                                fontSize: 11,
                            }}
                        />
                        <YAxis
                            tick={{ fontSize: 11, fill: "currentColor", opacity: 0.8 }}
                            width={52}
                            tickFormatter={(value) => formatSeconds(Number(value))}
                            label={{
                                value: "TTFT (seconds)",
                                angle: -90,
                                position: "insideLeft",
                                fill: "currentColor",
                                fontSize: 11,
                            }}
                        />
                        <Tooltip
                            content={({ active, payload }) => {
                                if (!active || !payload || payload.length === 0) return null;
                                const point = payload[0].payload as ProjectionDatum;
                                return (
                                    <div className="rounded-md border border-slate-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-xs shadow-lg">
                                        <div className="font-semibold text-slate-900 dark:text-zinc-100 mb-1">
                                            {point.concurrency} concurrent users
                                        </div>
                                        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-slate-600 dark:text-zinc-300">
                                            <span>Projected TTFT</span>
                                            <span className="text-right font-mono">{formatSeconds(point.ttftSeconds)}</span>
                                            <span>Reference TTFT</span>
                                            <span className="text-right font-mono">{formatSeconds(point.referenceTtftSeconds)}</span>
                                            <span>TPS / user</span>
                                            <span className="text-right font-mono">{formatTpsPerUser(point.tpsPerUser)}</span>
                                            <span>Total TPS</span>
                                            <span className="text-right font-mono">{point.totalTps.toFixed(1)}</span>
                                        </div>
                                    </div>
                                );
                            }}
                        />
                        <Legend
                            iconType="line"
                            wrapperStyle={{ fontSize: 11, paddingTop: 10, opacity: 0.9 }}
                        />
                        <Line
                            type="monotone"
                            name={poolName ? `${poolName} (Selected Pool)` : "Selected Pool"}
                            dataKey="ttftSeconds"
                            stroke="#6366f1"
                            strokeWidth={2.5}
                            dot={{ r: 3, fill: "#6366f1", strokeWidth: 0 }}
                            activeDot={{ r: 5 }}
                        >
                            <LabelList
                                dataKey="tpsPerUserLabel"
                                position="top"
                                fill="currentColor"
                                fontSize={10}
                                offset={10}
                            />
                        </Line>
                        <Line
                            type="monotone"
                            name="Headroom Reference"
                            dataKey="referenceTtftSeconds"
                            stroke="#f97316"
                            strokeDasharray="6 5"
                            strokeWidth={2}
                            dot={false}
                        />
                    </LineChart>
                </ResponsiveContainer>
            </div>

            <div className="mt-2 text-[10px] font-medium uppercase tracking-wider opacity-60">
                Concurrency levels: {DEFAULT_CONCURRENCY_LEVELS.join(", ")}
            </div>
        </div>
    );
}
