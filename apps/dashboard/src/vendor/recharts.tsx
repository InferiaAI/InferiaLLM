import { Children, isValidElement, type ReactNode } from "react";

type Datum = Record<string, unknown>;

interface SeriesProps {
    dataKey: string;
    stroke?: string;
    fill?: string;
    fillOpacity?: number;
    name?: string;
}

interface ChartProps {
    data: Datum[];
    children?: ReactNode;
}

interface ResponsiveContainerProps {
    width?: number | string;
    height?: number | string;
    children?: ReactNode;
}

const CHART_WIDTH = 960;
const CHART_HEIGHT = 320;
const CHART_PADDING = {
    top: 20,
    right: 20,
    bottom: 52,
    left: 56,
};
const X_LABEL_LIMIT = 6;

function toNumber(value: unknown): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
}

function extractSeries(children: ReactNode, typeName: string): SeriesProps[] {
    const series: SeriesProps[] = [];
    Children.forEach(children, (child) => {
        if (!isValidElement(child)) return;
        const displayName = (child.type as { displayName?: string }).displayName;
        if (displayName === typeName) {
            series.push(child.props as SeriesProps);
        }
    });
    return series;
}

function chartY(value: number, maxValue: number): number {
    const usableHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;
    if (maxValue <= 0) return CHART_HEIGHT - CHART_PADDING.bottom;
    return CHART_HEIGHT - CHART_PADDING.bottom - (value / maxValue) * usableHeight;
}

function chartX(index: number, total: number): number {
    if (total <= 1) return CHART_PADDING.left;
    const usableWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right;
    return CHART_PADDING.left + (index / (total - 1)) * usableWidth;
}

function linePoints(data: Datum[], dataKey: string, maxValue: number): string {
    return data
        .map((row, index) => `${chartX(index, data.length)},${chartY(toNumber(row[dataKey]), maxValue)}`)
        .join(" ");
}

function getBucketLabel(row: Datum, index: number): string {
    const raw = String(row.label ?? row.bucket_start ?? index + 1);
    return raw.length > 10 ? `${raw.slice(0, 10)}...` : raw;
}

function shouldRenderTick(index: number, total: number): boolean {
    if (total <= X_LABEL_LIMIT) return true;
    const step = Math.ceil(total / X_LABEL_LIMIT);
    return index % step === 0 || index === total - 1;
}

function renderAxes(data: Datum[], maxValue: number) {
    const baseline = CHART_HEIGHT - CHART_PADDING.bottom;
    const top = CHART_PADDING.top;
    const left = CHART_PADDING.left;
    const right = CHART_WIDTH - CHART_PADDING.right;
    const yTickCount = 4;
    const yTickValues = Array.from({ length: yTickCount + 1 }, (_, i) =>
        (maxValue * (yTickCount - i)) / yTickCount
    );

    return (
        <g>
            <line
                x1={left}
                y1={baseline}
                x2={right}
                y2={baseline}
                stroke="#4b5563"
                opacity={0.8}
                strokeWidth={1.4}
            />
            <line
                x1={left}
                y1={top}
                x2={left}
                y2={baseline}
                stroke="#4b5563"
                opacity={0.8}
                strokeWidth={1.4}
            />
            {yTickValues.map((value, idx) => {
                const y = chartY(value, maxValue);
                return (
                    <g key={`y-tick-${idx}`}>
                        <line
                            x1={left}
                            y1={y}
                            x2={right}
                            y2={y}
                            stroke="#374151"
                            opacity={0.45}
                            strokeWidth={1}
                            strokeDasharray="4 4"
                        />
                        <text
                            x={left - 10}
                            y={y + 4}
                            textAnchor="end"
                            fontSize="12"
                            fill="#9ca3af"
                        >
                            {Math.round(value)}
                        </text>
                    </g>
                );
            })}
            {data.map((row, index) => {
                if (!shouldRenderTick(index, data.length)) return null;
                const x = chartX(index, data.length);
                return (
                    <g key={`axis-${index}`}>
                        <line
                            x1={x}
                            y1={baseline}
                            x2={x}
                            y2={baseline + 6}
                            stroke="#6b7280"
                            opacity={0.85}
                            strokeWidth={1}
                        />
                        <text
                            x={x}
                            y={baseline + 24}
                            textAnchor="middle"
                            fontSize="13"
                            fill="#9ca3af"
                        >
                            {getBucketLabel(row, index)}
                        </text>
                    </g>
                );
            })}
        </g>
    );
}

export function ResponsiveContainer({
    width = "100%",
    height = 240,
    children,
}: ResponsiveContainerProps) {
    return (
        <div style={{ width, height }}>
            {children}
        </div>
    );
}

export function CartesianGrid() {
    return null;
}

export function XAxis() {
    return null;
}

export function YAxis() {
    return null;
}

export function Tooltip() {
    return null;
}

export function Legend() {
    return null;
}

export function Line() {
    return null;
}
Line.displayName = "RechartsLine";

export function Area() {
    return null;
}
Area.displayName = "RechartsArea";

export function Bar() {
    return null;
}
Bar.displayName = "RechartsBar";

export function LineChart({ data, children }: ChartProps) {
    const lines = extractSeries(children, "RechartsLine");
    const maxValue = Math.max(
        1,
        ...data.flatMap((row) => lines.map((line) => toNumber(row[line.dataKey])))
    );

    return (
        <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            width="100%"
            height="100%"
            preserveAspectRatio="none"
            role="img"
            aria-label="Line chart"
        >
            <rect x="0" y="0" width={CHART_WIDTH} height={CHART_HEIGHT} fill="transparent" />
            {renderAxes(data, maxValue)}
            {lines.map((line) => (
                <g key={line.dataKey}>
                    <polyline
                        points={linePoints(data, line.dataKey, maxValue)}
                        fill="none"
                        stroke={line.stroke || "#2563eb"}
                        strokeWidth={3}
                        strokeLinejoin="round"
                        strokeLinecap="round"
                        vectorEffect="non-scaling-stroke"
                    />
                    {data.map((row, index) => (
                        <circle
                            key={`${line.dataKey}-${index}`}
                            cx={chartX(index, data.length)}
                            cy={chartY(toNumber(row[line.dataKey]), maxValue)}
                            r={3}
                            fill={line.stroke || "#2563eb"}
                        />
                    ))}
                </g>
            ))}
        </svg>
    );
}

export function AreaChart({ data, children }: ChartProps) {
    const areas = extractSeries(children, "RechartsArea");
    const maxValue = Math.max(
        1,
        ...data.flatMap((row) => areas.map((area) => toNumber(row[area.dataKey])))
    );
    const baseline = CHART_HEIGHT - CHART_PADDING.bottom;

    return (
        <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            width="100%"
            height="100%"
            preserveAspectRatio="none"
            role="img"
            aria-label="Area chart"
        >
            <rect x="0" y="0" width={CHART_WIDTH} height={CHART_HEIGHT} fill="transparent" />
            {renderAxes(data, maxValue)}
            {areas.map((area) => {
                const points = linePoints(data, area.dataKey, maxValue);
                const firstX = chartX(0, data.length);
                const lastX = chartX(Math.max(data.length - 1, 0), data.length);
                return (
                    <g key={area.dataKey}>
                        <polygon
                            points={`${firstX},${baseline} ${points} ${lastX},${baseline}`}
                            fill={area.fill || area.stroke || "#3b82f6"}
                            fillOpacity={area.fillOpacity ?? 0.25}
                        />
                        <polyline
                            points={points}
                            fill="none"
                            stroke={area.stroke || "#3b82f6"}
                            strokeWidth={2.2}
                            strokeLinejoin="round"
                            strokeLinecap="round"
                            vectorEffect="non-scaling-stroke"
                        />
                    </g>
                );
            })}
        </svg>
    );
}

export function BarChart({ data, children }: ChartProps) {
    const bars = extractSeries(children, "RechartsBar");
    const maxValue = Math.max(
        1,
        ...data.flatMap((row) => bars.map((bar) => toNumber(row[bar.dataKey])))
    );
    const usableWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right;
    const groupWidth = data.length > 0 ? usableWidth / data.length : usableWidth;
    const barWidth = bars.length > 0 ? Math.max(groupWidth / (bars.length + 1), 8) : 12;
    const baseline = CHART_HEIGHT - CHART_PADDING.bottom;

    return (
        <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            width="100%"
            height="100%"
            preserveAspectRatio="none"
            role="img"
            aria-label="Bar chart"
        >
            <rect x="0" y="0" width={CHART_WIDTH} height={CHART_HEIGHT} fill="transparent" />
            {renderAxes(data, maxValue)}
            {data.map((row, dataIndex) =>
                bars.map((bar, barIndex) => {
                    const value = toNumber(row[bar.dataKey]);
                    const height = maxValue > 0 ? ((CHART_HEIGHT - CHART_PADDING * 2) * value) / maxValue : 0;
                    const x =
                        CHART_PADDING.left +
                        dataIndex * groupWidth +
                        barIndex * (barWidth + 4) +
                        2;
                    const y = baseline - height;
                    return (
                        <g key={`${dataIndex}-${bar.dataKey}`}>
                            <rect
                                x={x}
                                y={y}
                                width={barWidth}
                                height={Math.max(height, 0)}
                                fill={bar.fill || bar.stroke || "#2563eb"}
                                fillOpacity={bar.fillOpacity ?? 0.9}
                            />
                            {value > 0 && (
                                <text
                                    x={x + barWidth / 2}
                                    y={Math.max(y - 8, CHART_PADDING.top + 12)}
                                    textAnchor="middle"
                                    fontSize="12"
                                    fill="#9ca3af"
                                >
                                    {Math.round(value)}
                                </text>
                            )}
                        </g>
                    );
                })
            )}
        </svg>
    );
}
