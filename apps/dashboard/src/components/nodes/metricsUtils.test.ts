import { describe, it, expect } from "vitest";
import { formatBytes, formatBps, toChartRows, gpuSeries } from "./metricsUtils";
import type { NodeMetricsSample } from "@/services/workerService";

describe("formatBytes", () => {
  it("formats byte magnitudes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KiB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MiB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GiB");
  });

  it("formats sub-KiB values as plain bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1)).toBe("1 B");
  });

  it("returns 0 B for non-positive or non-finite input", () => {
    expect(formatBytes(-1)).toBe("0 B");
    expect(formatBytes(NaN)).toBe("0 B");
    expect(formatBytes(Infinity)).toBe("0 B");
  });
});

describe("formatBps", () => {
  it("appends /s", () => {
    expect(formatBps(0)).toBe("0 B/s");
    expect(formatBps(1024)).toBe("1.0 KiB/s");
  });
});

const sample = (over: Partial<NodeMetricsSample>): NodeMetricsSample => ({
  ts: "2026-06-16T00:00:00Z", cpu_pct: 0, mem_used_bytes: 0, mem_total_bytes: 0,
  net_rx_bps: 0, net_tx_bps: 0, disk_read_bps: 0, disk_write_bps: 0, gpus: [],
  ...over,
});

describe("toChartRows", () => {
  it("maps samples to chart rows with a short time label", () => {
    const rows = toChartRows([sample({ cpu_pct: 12.3, mem_used_bytes: 1024 })]);
    expect(rows).toHaveLength(1);
    expect(rows[0].cpu_pct).toBe(12.3);
    expect(typeof rows[0].label).toBe("string");
    expect(rows[0].mem_used_gib).toBeCloseTo(1024 / 1024 ** 3, 6);
  });

  it("handles an empty list", () => {
    expect(toChartRows([])).toEqual([]);
  });

  it("falls back to raw ts when timestamp has no time component", () => {
    const rows = toChartRows([sample({ ts: "no-time-here" })]);
    expect(rows[0].label).toBe("no-time-here");
  });

  it("includes gpu util and vram keys when gpus are present", () => {
    const rows = toChartRows([
      sample({
        gpus: [
          { index: 0, name: "A100", util_pct: 55, mem_used_mib: 2048, mem_total_mib: 4096 },
        ],
      }),
    ]);
    expect(rows[0].gpu0_util).toBe(55);
    expect(rows[0].gpu0_vram_gib).toBeCloseTo(2048 / 1024, 6);
  });
});

describe("gpuSeries", () => {
  it("returns one series descriptor per GPU index from the latest sample", () => {
    const series = gpuSeries([
      sample({ gpus: [
        { index: 0, name: "A100", util_pct: 10, mem_used_mib: 1, mem_total_mib: 2 },
        { index: 1, name: "A100", util_pct: 20, mem_used_mib: 3, mem_total_mib: 4 },
      ] }),
    ]);
    expect(series.map((s) => s.key)).toEqual(["gpu0_util", "gpu1_util"]);
    expect(series[0].label).toContain("GPU 0");
  });

  it("returns [] when there are no gpus", () => {
    expect(gpuSeries([sample({})])).toEqual([]);
    expect(gpuSeries([])).toEqual([]);
  });
});
