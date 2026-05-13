import { useState } from "react";
import { Copy, X, Loader2, ServerCog, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  mintBootstrapToken,
  type MintBootstrapTokenResponse,
} from "@/services/workerService";

interface Props {
  poolId: string;
  poolName?: string;
  onClose: () => void;
}

const COMPOSE_INSTRUCTIONS = `# On a fresh GPU host with Docker + NVIDIA Container Toolkit:
# 1. Clone the inferia-worker repo
# 2. cp .env.sample .env
# 3. Paste the values above into .env
# 4. Fill in NODE_NAME and WORKER_ADVERTISE_URL for this host
# 5. docker compose up -d
#
# The worker will appear in this pool's Workers tab within ~10 seconds.`;

export default function AddWorkerModal({ poolId, poolName, onClose }: Props) {
  const [ttlHours, setTtlHours] = useState(1);
  const [result, setResult] = useState<MintBootstrapTokenResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleGenerate = async () => {
    setLoading(true);
    try {
      const res = await mintBootstrapToken({ pool_id: poolId, ttl_hours: ttlHours });
      setResult(res);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to generate bootstrap token");
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied to clipboard`);
    } catch {
      toast.error("Clipboard unavailable; select and copy manually");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl rounded-xl border bg-background shadow-xl p-6 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 rounded-full bg-ember-500/10 p-2">
              <ServerCog className="h-5 w-5 text-ember-500" />
            </div>
            <div>
              <h3 className="text-lg font-semibold">Add worker to pool</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Generates a bootstrap token that a fresh inferia-worker host
                exchanges for a long-lived agent JWT on first start.
                {poolName && (
                  <>
                    {" "}Target pool: <span className="font-mono">{poolName}</span>
                  </>
                )}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Step 1: pick TTL + generate */}
        {!result && (
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium block mb-1.5">
                Token TTL (hours)
              </label>
              <input
                type="number"
                min={1}
                max={24}
                value={ttlHours}
                onChange={(e) => setTtlHours(Number(e.target.value))}
                className="h-9 w-32 rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                1–24 hours. The token is single-pool scoped and expires after
                this many hours regardless of use.
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={handleGenerate}
                disabled={loading || ttlHours < 1 || ttlHours > 24}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-md text-white inline-flex items-center gap-2",
                  loading
                    ? "bg-ember-600/60 cursor-not-allowed"
                    : "bg-ember-600 hover:bg-ember-700",
                )}
              >
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                Generate token
              </button>
            </div>
          </div>
        )}

        {/* Step 2: show env snippet */}
        {result && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Token issued. Expires{" "}
              <span className="font-mono">
                {new Date(result.expires_at * 1000).toLocaleString()}
              </span>
              .
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-sm font-medium">Worker .env</label>
                <button
                  onClick={() => handleCopy(result.env_snippet, ".env snippet")}
                  className="text-xs inline-flex items-center gap-1.5 text-ember-600 hover:text-ember-700"
                >
                  <Copy className="h-3.5 w-3.5" /> Copy
                </button>
              </div>
              <pre className="rounded-md border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                {result.env_snippet}
              </pre>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-sm font-medium">Next steps</label>
                <button
                  onClick={() => handleCopy(COMPOSE_INSTRUCTIONS, "Instructions")}
                  className="text-xs inline-flex items-center gap-1.5 text-ember-600 hover:text-ember-700"
                >
                  <Copy className="h-3.5 w-3.5" /> Copy
                </button>
              </div>
              <pre className="rounded-md border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap">
                {COMPOSE_INSTRUCTIONS}
              </pre>
            </div>

            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400">
              Treat the bootstrap token as a secret. It grants the holder the
              ability to register a worker into this pool until it expires.
              Anyone with the resulting worker JWT can serve inference on
              behalf of this pool.
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setResult(null);
                }}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                Generate another
              </button>
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-sm rounded-md bg-ember-600 hover:bg-ember-700 text-white"
              >
                Done
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
