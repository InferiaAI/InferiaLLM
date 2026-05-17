import { useState } from "react";
import { X, Plus, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface Props {
  labels: Record<string, string>;
  onAdd: (k: string, v: string) => Promise<void>;
  onRemove: (k: string) => Promise<void>;
  disabled?: boolean;
}

const MAX_LABELS = 32;
const MAX_KEY = 253;
const MAX_VAL = 253;

export default function LabelEditor({ labels, onAdd, onRemove, disabled }: Props) {
  const [draftK, setDraftK] = useState("");
  const [draftV, setDraftV] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  const handleAdd = async () => {
    const k = draftK.trim();
    const v = draftV.trim();
    if (!k) {
      toast.error("label key is required");
      return;
    }
    if (k.length > MAX_KEY || v.length > MAX_VAL) {
      toast.error(`max length: key ${MAX_KEY}, value ${MAX_VAL}`);
      return;
    }
    if (Object.keys(labels).length >= MAX_LABELS && !(k in labels)) {
      toast.error(`max ${MAX_LABELS} labels per node`);
      return;
    }
    setBusy(true);
    try {
      await onAdd(k, v);
      setDraftK("");
      setDraftV("");
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to add label");
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (k: string) => {
    setRemoving(k);
    try {
      await onRemove(k);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to remove label");
    } finally {
      setRemoving(null);
    }
  };

  const entries = Object.entries(labels);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {entries.length === 0 && (
          <span className="text-sm text-muted-foreground">No labels yet.</span>
        )}
        {entries.map(([k, v]) => (
          <span
            key={k}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border bg-muted/30 text-xs font-mono"
          >
            <span className="font-medium">{k}</span>={v}
            {!disabled && (
              <button
                onClick={() => handleRemove(k)}
                disabled={removing === k}
                className={cn(
                  "rounded p-0.5",
                  removing === k
                    ? "text-muted-foreground cursor-wait"
                    : "text-muted-foreground hover:text-red-500 hover:bg-red-500/10",
                )}
                title={`Remove ${k}`}
              >
                {removing === k ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <X className="w-3 h-3" />
                )}
              </button>
            )}
          </span>
        ))}
      </div>
      {!disabled && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            placeholder="key"
            className="h-9 w-40 rounded-md border bg-card px-3 text-sm font-mono outline-none focus:ring-1 focus:ring-ember-500"
            value={draftK}
            onChange={(e) => setDraftK(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void handleAdd();
              }
            }}
          />
          <span className="text-muted-foreground">=</span>
          <input
            placeholder="value"
            className="h-9 w-40 rounded-md border bg-card px-3 text-sm font-mono outline-none focus:ring-1 focus:ring-ember-500"
            value={draftV}
            onChange={(e) => setDraftV(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void handleAdd();
              }
            }}
          />
          <button
            onClick={() => void handleAdd()}
            disabled={busy || !draftK.trim()}
            className={cn(
              "h-9 px-3 inline-flex items-center gap-1 text-sm rounded-md text-white",
              busy || !draftK.trim()
                ? "bg-ember-600/60 cursor-not-allowed"
                : "bg-ember-600 hover:bg-ember-700",
            )}
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            Add
          </button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {entries.length}/{MAX_LABELS} labels. Keys up to {MAX_KEY} chars; values up to {MAX_VAL}.
      </p>
    </div>
  );
}
