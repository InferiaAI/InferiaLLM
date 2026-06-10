import { useEffect, useMemo, useRef, useState } from "react";
import type { InstanceType } from "@/hooks/useInstanceCatalog";

function priceLabel(p: number | null): string {
  return p != null && p > 0 ? `$${p.toFixed(3)}/hr` : "price N/A";
}

function summary(it: InstanceType): string {
  return it.gpu_count > 0
    ? `${it.name} — ${it.gpu_model ?? "GPU"} ${it.gpu_ram_gb}GB · ${it.gpu_count} GPU · ${priceLabel(it.price_per_hour)}`
    : `${it.name} — ${it.vcpu} vCPU · ${it.ram_gb}GB · ${priceLabel(it.price_per_hour)}`;
}

// Smallest instance first: ascending by vCPU, then RAM, then GPU count.
function bySize(a: InstanceType, b: InstanceType): number {
  return a.vcpu - b.vcpu || a.ram_gb - b.ram_gb || a.gpu_count - b.gpu_count;
}

export function InstanceDropdown({
  instances,
  value,
  onSelect,
  loading,
}: {
  instances: InstanceType[];
  value: string | null;
  onSelect: (it: InstanceType) => void;
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // GPU instances are the common case for this platform — default to showing
  // only them; the toggle reveals CPU instances too.
  const [gpuOnly, setGpuOnly] = useState(true);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Filter by the GPU-only toggle, then order smallest-first. The selected
  // summary is looked up from the FULL list so a selection the filter hides
  // still renders in the trigger.
  const shown = useMemo(
    () => instances.filter((i) => !gpuOnly || i.gpu_count > 0).slice().sort(bySize),
    [instances, gpuOnly],
  );
  const selected = instances.find((i) => i.name === value) ?? null;

  return (
    <div ref={ref} className="relative" data-testid="instance-dropdown">
      <label className="mb-1.5 flex items-center gap-2 text-xs text-muted-foreground select-none">
        <input
          type="checkbox"
          checked={gpuOnly}
          onChange={(e) => setGpuOnly(e.target.checked)}
          data-testid="gpu-only-toggle"
          className="h-3.5 w-3.5 rounded border-input accent-primary"
        />
        GPU only
      </label>

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={loading}
        data-testid="instance-dropdown-trigger"
        className="w-full flex items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm text-left disabled:opacity-50"
      >
        <span className={selected ? "" : "text-muted-foreground"}>
          {loading
            ? "Loading instance types…"
            : selected
            ? summary(selected)
            : "Select an instance type"}
        </span>
        <span className="ml-2 text-muted-foreground">▾</span>
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 w-full max-h-80 overflow-y-auto rounded-md border border-border bg-card shadow-lg"
          data-testid="instance-dropdown-list"
        >
          {shown.length === 0 ? (
            <div className="px-3 py-4 text-xs text-muted-foreground">
              {gpuOnly ? "No GPU instance types available" : "No instance types available"}
            </div>
          ) : (
            shown.map((it) => (
              <button
                key={it.name}
                type="button"
                data-testid={`inst-option-${it.name}`}
                data-selected={it.name === value ? "true" : "false"}
                onClick={() => {
                  onSelect(it);
                  setOpen(false);
                }}
                className={`w-full text-left px-3 py-2 border-b border-border/50 hover:bg-accent ${
                  it.name === value ? "bg-accent" : ""
                }`}
              >
                <div className="font-medium text-sm">{it.name}</div>
                {it.gpu_count > 0 && (
                  <div className="text-xs text-muted-foreground">
                    {it.gpu_model ?? "GPU"} · {it.gpu_ram_gb}GB VRAM · {it.gpu_count} GPU
                  </div>
                )}
                <div className="text-xs text-muted-foreground">
                  {it.vcpu} vCPU · {it.ram_gb}GB RAM
                </div>
                <div className="text-xs font-medium">{priceLabel(it.price_per_hour)}</div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
