import type { NodeMetricsSample } from "@/services/workerService";

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"];

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return i === 0 ? `${v} ${UNITS[i]}` : `${v.toFixed(1)} ${UNITS[i]}`;
}

export function formatBps(n: number): string {
  return `${formatBytes(n)}/s`;
}

export interface ChartRow {
  label: string;
  cpu_pct: number;
  mem_used_gib: number;
  mem_total_gib: number;
  net_rx_bps: number;
  net_tx_bps: number;
  disk_read_bps: number;
  disk_write_bps: number;
  [gpuKey: string]: number | string;
}

function shortTime(ts: string): string {
  // "2026-06-16T12:34:56Z" -> "12:34:56". Falls back to the raw string.
  const m = ts.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts;
}

export function toChartRows(samples: NodeMetricsSample[]): ChartRow[] {
  return samples.map((s) => {
    const row: ChartRow = {
      label: shortTime(s.ts),
      cpu_pct: s.cpu_pct,
      mem_used_gib: s.mem_used_bytes / 1024 ** 3,
      mem_total_gib: s.mem_total_bytes / 1024 ** 3,
      net_rx_bps: s.net_rx_bps,
      net_tx_bps: s.net_tx_bps,
      disk_read_bps: s.disk_read_bps,
      disk_write_bps: s.disk_write_bps,
    };
    for (const g of s.gpus) {
      row[`gpu${g.index}_util`] = g.util_pct;
      row[`gpu${g.index}_vram_gib`] = g.mem_used_mib / 1024;
    }
    return row;
  });
}

export interface GpuSeriesDescriptor {
  key: string;
  vramKey: string;
  label: string;
  name: string;
}

export function gpuSeries(samples: NodeMetricsSample[]): GpuSeriesDescriptor[] {
  const latest = samples.length ? samples[samples.length - 1] : null;
  if (!latest || latest.gpus.length === 0) return [];
  return latest.gpus.map((g) => ({
    key: `gpu${g.index}_util`,
    vramKey: `gpu${g.index}_vram_gib`,
    label: `GPU ${g.index}`,
    name: g.name,
  }));
}
