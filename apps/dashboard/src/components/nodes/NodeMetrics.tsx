import { useQuery } from "@tanstack/react-query";
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getNodeMetrics } from "@/services/workerService";
import { formatBpsTick, gpuSeries, toChartRows } from "./metricsUtils";

interface NodeMetricsProps {
  nodeId: string;
  nodeState?: string;
  currentPhase?: string | null;
}

const GPU_COLORS = ["#2563eb", "#db2777", "#16a34a", "#9333ea", "#ea580c", "#0891b2"];

function Panel({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-4">
      <h3 className="font-mono text-sm font-semibold mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={220}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

// Thin wrapper keeps the not-ready early-return free of hooks (Rules of Hooks).
export default function NodeMetrics({ nodeId, nodeState, currentPhase }: NodeMetricsProps) {
  if (nodeState && nodeState !== "ready") {
    return (
      <div className="rounded-md border bg-card p-6 text-sm text-muted-foreground">
        Metrics available once the worker registers. Currently {currentPhase ?? "pending"}…
      </div>
    );
  }
  return <NodeMetricsLive nodeId={nodeId} />;
}

function NodeMetricsLive({ nodeId }: { nodeId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["node-metrics", nodeId],
    queryFn: () => getNodeMetrics(nodeId),
    refetchInterval: 5000,
  });

  if (isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Loading metrics…</div>;
  }
  if (isError) {
    return <div className="p-6 text-sm text-red-500">Couldn’t load metrics for this node.</div>;
  }

  const samples = data?.samples ?? [];
  if (samples.length === 0) {
    return (
      <div className="rounded-md border bg-card p-6 text-sm text-muted-foreground">
        Waiting for the first metrics sample from the worker…
      </div>
    );
  }

  const rows = toChartRows(samples);
  const gpus = gpuSeries(samples);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Panel title="CPU Utilization (%)">
        <LineChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} />
          <Tooltip />
          <Line type="monotone" dataKey="cpu_pct" stroke="#2563eb" strokeWidth={2} dot={false} name="CPU %" />
        </LineChart>
      </Panel>

      <Panel title="Memory (GiB)">
        <AreaChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Area type="monotone" dataKey="mem_used_gib" stroke="#16a34a" fill="#16a34a" fillOpacity={0.35} name="Used GiB" />
        </AreaChart>
      </Panel>

      {gpus.length > 0 && (
        <Panel title={`GPU Utilization (%) — ${gpus.map((g) => g.label).join(", ")}`}>
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
            <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} />
            <Tooltip />
            <Legend />
            {gpus.map((g, i) => (
              <Line key={g.key} type="monotone" dataKey={g.key} stroke={GPU_COLORS[i % GPU_COLORS.length]} strokeWidth={2} dot={false} name={`${g.label} (${g.name})`} />
            ))}
          </LineChart>
        </Panel>
      )}

      {gpus.length > 0 && (
        <Panel title="GPU VRAM (GiB)">
          <AreaChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            {gpus.map((g, i) => (
              <Area key={g.vramKey} type="monotone" dataKey={g.vramKey} stroke={GPU_COLORS[i % GPU_COLORS.length]} fill={GPU_COLORS[i % GPU_COLORS.length]} fillOpacity={0.25} name={`${g.label} VRAM`} />
            ))}
          </AreaChart>
        </Panel>
      )}

      <Panel title="Network (per second)">
        <LineChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={formatBpsTick} width={80} />
          <Tooltip formatter={formatBpsTick} />
          <Legend />
          <Line type="monotone" dataKey="net_rx_bps" stroke="#0891b2" strokeWidth={2} dot={false} name="RX" />
          <Line type="monotone" dataKey="net_tx_bps" stroke="#ea580c" strokeWidth={2} dot={false} name="TX" />
        </LineChart>
      </Panel>

      <Panel title="Disk I/O (per second)">
        <LineChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={formatBpsTick} width={80} />
          <Tooltip formatter={formatBpsTick} />
          <Legend />
          <Line type="monotone" dataKey="disk_read_bps" stroke="#9333ea" strokeWidth={2} dot={false} name="Read" />
          <Line type="monotone" dataKey="disk_write_bps" stroke="#db2777" strokeWidth={2} dot={false} name="Write" />
        </LineChart>
      </Panel>
    </div>
  );
}
