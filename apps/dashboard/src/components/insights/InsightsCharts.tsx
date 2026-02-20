import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Line,
    LineChart,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

const PIE_COLORS = ["#2563eb", "#db2777", "#16a34a", "#9333ea", "#ea580c", "#0891b2"];

function formatNumber(value: number, maxFractionDigits = 2): string {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: maxFractionDigits }).format(value);
}

export function RequestVolumeChart({ data }: { data: any[] }) {
    return (
        <ResponsiveContainer width="100%" height={260}>
            <LineChart data={data}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={18} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Line
                    type="monotone"
                    dataKey="requests"
                    stroke="#2563eb"
                    strokeWidth={2}
                    dot={false}
                    name="Requests"
                />
            </LineChart>
        </ResponsiveContainer>
    );
}

export function TokenUsageChart({ data, isEmbedding }: { data: any[]; isEmbedding: boolean }) {
    if (isEmbedding) {
        return (
            <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={18} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip />
                    <Area
                        type="monotone"
                        dataKey="total_tokens"
                        stroke="#16a34a"
                        fill="#16a34a"
                        fillOpacity={0.35}
                        name="Tokens"
                    />
                </AreaChart>
            </ResponsiveContainer>
        );
    }

    return (
        <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={data}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={18} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Area
                    type="monotone"
                    dataKey="prompt_tokens"
                    stackId="tokens"
                    stroke="#16a34a"
                    fill="#16a34a"
                    fillOpacity={0.35}
                    name="Prompt"
                />
                <Area
                    type="monotone"
                    dataKey="completion_tokens"
                    stackId="tokens"
                    stroke="#3b82f6"
                    fill="#3b82f6"
                    fillOpacity={0.35}
                    name="Completion"
                />
            </AreaChart>
        </ResponsiveContainer>
    );
}

export function ModelDistributionChart({ data }: { data: any[] }) {
    return (
        <ResponsiveContainer width="100%" height={260}>
            <PieChart>
                <Pie
                    data={data}
                    dataKey="requests"
                    nameKey="model"
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={2}
                    label={({ percent }) => `${(percent * 100).toFixed(0)}%`}
                    labelLine={false}
                >
                    {data.map((entry, index) => (
                        <Cell
                            key={entry.model}
                            fill={PIE_COLORS[index % PIE_COLORS.length]}
                        />
                    ))}
                </Pie>
                <Tooltip
                    content={({ active, payload }) => {
                        if (active && payload && payload.length) {
                            const data = payload[0].payload;
                            return (
                                <div className="rounded-lg border bg-background p-2 shadow-sm">
                                    <div className="flex flex-col gap-1">
                                        <span className="font-bold text-muted-foreground">
                                            {data.model}
                                        </span>
                                        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                                            <span className="text-muted-foreground">Requests:</span>
                                            <span className="font-mono">{formatNumber(data.requests, 0)}</span>
                                            <span className="text-muted-foreground">Tokens:</span>
                                            <span className="font-mono">{formatNumber(data.total_tokens, 0)}</span>
                                        </div>
                                    </div>
                                </div>
                            );
                        }
                        return null;
                    }}
                />
                <Legend
                    layout="vertical"
                    verticalAlign="middle"
                    align="right"
                    wrapperStyle={{ fontSize: "11px", maxWidth: "120px" }}
                />
            </PieChart>
        </ResponsiveContainer>
    );
}

export function TrafficByIpChart({ data }: { data: any[] }) {
    return (
        <ResponsiveContainer width="100%" height={260}>
            <BarChart
                data={data}
                layout="vertical"
                margin={{ top: 0, right: 30, left: 30, bottom: 0 }}
            >
                <CartesianGrid strokeDasharray="3 3" opacity={0.2} horizontal={true} vertical={false} />
                <XAxis type="number" hide />
                <YAxis
                    dataKey="ip_address"
                    type="category"
                    tick={{ fontSize: 11 }}
                    width={100}
                    tickFormatter={(ip) => (ip ? ip.slice(0, 15) : "Unknown")}
                    interval={0}
                />
                <Tooltip
                    cursor={{ fill: "transparent" }}
                    content={({ active, payload }) => {
                        if (active && payload && payload.length) {
                            const data = payload[0].payload;
                            return (
                                <div className="rounded-lg border bg-background p-2 shadow-sm z-50 relative">
                                    <div className="grid grid-cols-2 gap-2">
                                        <div className="flex flex-col">
                                            <span className="text-[0.70rem] uppercase text-muted-foreground">
                                                IP Address
                                            </span>
                                            <span className="font-bold text-muted-foreground">
                                                {data.ip_address}
                                            </span>
                                        </div>
                                        <div className="flex flex-col">
                                            <span className="text-[0.70rem] uppercase text-muted-foreground">
                                                Success Rate
                                            </span>
                                            <span className="font-bold">
                                                {data.success_rate.toFixed(1)}%
                                            </span>
                                        </div>
                                        <div className="flex flex-col">
                                            <span className="text-[0.70rem] uppercase text-muted-foreground">
                                                Tokens
                                            </span>
                                            <span className="font-bold">
                                                {formatNumber(data.total_tokens, 0)}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            );
                        }
                        return null;
                    }}
                />
                <Bar
                    dataKey="requests"
                    fill="#9333ea"
                    radius={[0, 4, 4, 0]}
                    name="Requests"
                    barSize={16}
                    animationDuration={1000}
                />
            </BarChart>
        </ResponsiveContainer>
    );
}
