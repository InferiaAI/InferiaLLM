import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  HardDrive, Globe, Cpu, ArrowRight, Loader2, Copy, X, CheckCircle2, Plus,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  addWorkerNode, addProviderNode,
  type AddWorkerNodeResponse,
} from "@/services/nodeService";

type Provider = "worker" | "nosana" | "akash";

const PROVIDER_CARDS: Array<{
  id: Provider;
  name: string;
  description: string;
  icon: typeof HardDrive;
  color: string;
}> = [
  {
    id: "worker",
    name: "Self-hosted (inferia-worker)",
    description:
      "A GPU host you control: bare-metal, your own server, or a cloud VM you spin up. " +
      "We mint a bootstrap token; you paste it into the worker's .env and run `docker compose up`.",
    icon: HardDrive,
    color: "text-ember-500 bg-ember-500/10",
  },
  {
    id: "nosana",
    name: "Nosana Network",
    description:
      "Decentralized GPU compute grid. Submit one Nosana job to add a single node.",
    icon: Globe,
    color: "text-green-500 bg-green-500/10",
  },
  {
    id: "akash",
    name: "Akash Network",
    description:
      "Decentralized cloud compute. Submit one Akash deployment to add a single node.",
    icon: Cpu,
    color: "text-purple-500 bg-purple-500/10",
  },
];

export default function NewNode() {
  const navigate = useNavigate();
  const [step, setStep] = useState<"pick" | "form">("pick");
  const [provider, setProvider] = useState<Provider | null>(null);

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Add Node</h2>
        <p className="text-muted-foreground mt-2">
          Nodes are the unit of compute in InferiaLLM. Pick a provider; each registers
          exactly one node which then shows up in <span className="font-mono">Compute Nodes</span>.
        </p>
      </div>

      {step === "pick" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {PROVIDER_CARDS.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setProvider(p.id);
                setStep("form");
              }}
              className="text-left group relative p-6 rounded-xl border bg-card dark:border-border hover:border-ember-500/50 dark:hover:border-ember-500/50 transition-colors hover:shadow-md flex flex-col gap-4"
            >
              <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center transition-colors", p.color)}>
                <p.icon className="w-6 h-6" />
              </div>
              <div>
                <h3 className="font-bold text-lg mb-1 group-hover:text-ember-600 dark:group-hover:text-ember-400 transition-colors">
                  {p.name}
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{p.description}</p>
              </div>
              <span className="absolute top-4 right-4 text-muted-foreground group-hover:text-ember-500">
                <ArrowRight className="w-4 h-4" />
              </span>
            </button>
          ))}
        </div>
      )}

      {step === "form" && provider === "worker" && (
        <WorkerForm onBack={() => setStep("pick")} onDone={() => navigate("/dashboard/compute/nodes")} />
      )}
      {step === "form" && provider === "nosana" && (
        <DePINForm
          provider="nosana"
          onBack={() => setStep("pick")}
          onDone={(id) => navigate(`/dashboard/compute/nodes/${id}`)}
        />
      )}
      {step === "form" && provider === "akash" && (
        <DePINForm
          provider="akash"
          onBack={() => setStep("pick")}
          onDone={(id) => navigate(`/dashboard/compute/nodes/${id}`)}
        />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Self-hosted form.
// ---------------------------------------------------------------------------


function WorkerForm({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [advertiseUrl, setAdvertiseUrl] = useState("");
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [draftK, setDraftK] = useState("");
  const [draftV, setDraftV] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AddWorkerNodeResponse | null>(null);

  const addLabel = () => {
    const k = draftK.trim();
    const v = draftV.trim();
    if (!k) {
      toast.error("label key required");
      return;
    }
    setLabels({ ...labels, [k]: v });
    setDraftK("");
    setDraftV("");
  };
  const removeLabel = (k: string) => {
    const { [k]: _, ...rest } = labels;
    setLabels(rest);
  };

  const handleSubmit = async () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    setLoading(true);
    try {
      const r = await addWorkerNode({
        node_name: name.trim(),
        advertise_url: advertiseUrl.trim() || undefined,
        labels,
      });
      setResult(r);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to add worker");
    } finally {
      setLoading(false);
    }
  };

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Clipboard unavailable");
    }
  };

  if (result) {
    return (
      <div className="rounded-xl border bg-card p-6 space-y-4">
        <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="w-4 h-4" />
          Token issued. Expires{" "}
          <span className="font-mono">{new Date(result.expires_at * 1000).toLocaleString()}</span>.
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-sm font-medium">.env (paste into the worker host)</label>
            <button
              onClick={() => copy(result.env_snippet, ".env")}
              className="text-xs inline-flex items-center gap-1.5 text-ember-600 hover:text-ember-700"
            >
              <Copy className="w-3.5 h-3.5" /> Copy
            </button>
          </div>
          <pre className="rounded-md border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
            {result.env_snippet}
          </pre>
        </div>
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400">
          Treat the bootstrap token as a secret. After pasting the .env, run{" "}
          <span className="font-mono">docker compose up -d</span> on the worker host. The
          new node appears in this list as soon as the worker registers.
        </div>
        <div className="flex justify-end">
          <button
            onClick={onDone}
            className="px-3 py-1.5 text-sm rounded-md bg-ember-600 hover:bg-ember-700 text-white"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-6 space-y-5">
      <h3 className="text-lg font-semibold inline-flex items-center gap-2">
        <HardDrive className="w-5 h-5 text-ember-500" /> Self-hosted (inferia-worker)
      </h3>
      <p className="text-sm text-muted-foreground">
        Workers self-register, so this only takes a name. After submit you'll get the .env to
        paste into the GPU host's <span className="font-mono">inferia-worker</span> deploy.
      </p>

      <Field label="Node name">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="dc1-gpu-01"
          className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
        />
      </Field>

      <Field label="Advertise URL (optional)">
        <input
          value={advertiseUrl}
          onChange={(e) => setAdvertiseUrl(e.target.value)}
          placeholder="https://gpu-host.example.com:8080"
          className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
        />
        <p className="text-xs text-muted-foreground mt-1">
          The control plane will use this URL to send inference traffic. You can fill it in the
          worker's .env later if you'd rather.
        </p>
      </Field>

      <Field label="Labels">
        <LabelBuilder
          labels={labels}
          draftK={draftK} setDraftK={setDraftK}
          draftV={draftV} setDraftV={setDraftV}
          onAdd={addLabel} onRemove={removeLabel}
        />
      </Field>

      <div className="flex items-center justify-between pt-2">
        <button onClick={onBack} className="px-4 py-2 text-sm rounded-md border hover:bg-muted">
          Back
        </button>
        <button
          onClick={handleSubmit}
          disabled={loading || !name.trim()}
          className={cn(
            "px-4 py-2 text-sm rounded-md text-white inline-flex items-center gap-2",
            loading || !name.trim() ? "bg-ember-600/60 cursor-not-allowed" : "bg-ember-600 hover:bg-ember-700",
          )}
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
          {loading ? "Issuing token…" : "Generate token"}
        </button>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// DePIN (Nosana / Akash) form.
// ---------------------------------------------------------------------------


function DePINForm({
  provider, onBack, onDone,
}: { provider: "nosana" | "akash"; onBack: () => void; onDone: (nodeId: string) => void }) {
  const [name, setName] = useState("");
  const [gpuType, setGpuType] = useState("");
  const [marketAddress, setMarketAddress] = useState("");
  const [credential, setCredential] = useState("default");
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [draftK, setDraftK] = useState("");
  const [draftV, setDraftV] = useState("");
  const [loading, setLoading] = useState(false);

  const addLabel = () => {
    if (!draftK.trim()) return;
    setLabels({ ...labels, [draftK.trim()]: draftV.trim() });
    setDraftK("");
    setDraftV("");
  };
  const removeLabel = (k: string) => {
    const { [k]: _, ...rest } = labels;
    setLabels(rest);
  };

  const handleSubmit = async () => {
    if (!gpuType.trim()) {
      toast.error("GPU type is required");
      return;
    }
    if (provider === "nosana" && !marketAddress.trim()) {
      toast.error("Market address is required for Nosana");
      return;
    }
    setLoading(true);
    try {
      const r = await addProviderNode(provider, {
        node_name: name.trim() || undefined,
        labels,
        credential_name: credential || undefined,
        spec: {
          gpu_type: gpuType.trim(),
          ...(provider === "nosana" ? { market_address: marketAddress.trim() } : {}),
        },
      });
      toast.success(`${provider} node submitted (${r.state})`);
      onDone(r.node_id);
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || `Failed to add ${provider} node`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-6 space-y-5">
      <h3 className="text-lg font-semibold capitalize">{provider} node</h3>
      <p className="text-sm text-muted-foreground">
        We'll submit one {provider} {provider === "nosana" ? "job" : "deployment"} and persist
        it as a node in this org's inventory. Use labels to group it later (e.g.{" "}
        <span className="font-mono">env=staging</span>).
      </p>

      <Field label="Node name (optional)">
        <input
          value={name} onChange={(e) => setName(e.target.value)}
          placeholder="nosana-eu-01"
          className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
        />
      </Field>

      <Field label="GPU type">
        <input
          value={gpuType} onChange={(e) => setGpuType(e.target.value)}
          placeholder="RTX 4090"
          className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
        />
      </Field>

      {provider === "nosana" && (
        <Field label="Market address">
          <input
            value={marketAddress} onChange={(e) => setMarketAddress(e.target.value)}
            placeholder="ABCDEF…"
            className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500 font-mono"
          />
        </Field>
      )}

      <Field label="Credential name">
        <input
          value={credential} onChange={(e) => setCredential(e.target.value)}
          placeholder="default"
          className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Provider credential to use (configured under Settings → Providers).
        </p>
      </Field>

      <Field label="Labels">
        <LabelBuilder
          labels={labels}
          draftK={draftK} setDraftK={setDraftK}
          draftV={draftV} setDraftV={setDraftV}
          onAdd={addLabel} onRemove={removeLabel}
        />
      </Field>

      <div className="flex items-center justify-between pt-2">
        <button onClick={onBack} className="px-4 py-2 text-sm rounded-md border hover:bg-muted">
          Back
        </button>
        <button
          onClick={handleSubmit}
          disabled={loading}
          className={cn(
            "px-4 py-2 text-sm rounded-md text-white inline-flex items-center gap-2",
            loading ? "bg-ember-600/60 cursor-not-allowed" : "bg-ember-600 hover:bg-ember-700",
          )}
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
          {loading ? "Submitting…" : "Add node"}
        </button>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Shared helpers.
// ---------------------------------------------------------------------------


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium">{label}</label>
      {children}
    </div>
  );
}


function LabelBuilder({
  labels, draftK, draftV, setDraftK, setDraftV, onAdd, onRemove,
}: {
  labels: Record<string, string>;
  draftK: string; draftV: string;
  setDraftK: (s: string) => void; setDraftV: (s: string) => void;
  onAdd: () => void; onRemove: (k: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 min-h-[28px]">
        {Object.entries(labels).map(([k, v]) => (
          <span
            key={k}
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded border bg-muted/30 text-xs font-mono"
          >
            {k}={v}
            <button
              onClick={() => onRemove(k)}
              className="opacity-60 hover:opacity-100"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        {Object.keys(labels).length === 0 && (
          <span className="text-xs text-muted-foreground">No labels yet.</span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={draftK} onChange={(e) => setDraftK(e.target.value)}
          placeholder="key"
          className="h-9 w-40 rounded-md border bg-card px-3 text-sm font-mono outline-none focus:ring-1 focus:ring-ember-500"
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onAdd(); } }}
        />
        <span className="text-muted-foreground">=</span>
        <input
          value={draftV} onChange={(e) => setDraftV(e.target.value)}
          placeholder="value"
          className="h-9 w-40 rounded-md border bg-card px-3 text-sm font-mono outline-none focus:ring-1 focus:ring-ember-500"
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onAdd(); } }}
        />
        <button
          onClick={onAdd}
          disabled={!draftK.trim()}
          className={cn(
            "h-9 px-3 inline-flex items-center gap-1 text-sm rounded-md text-white",
            !draftK.trim() ? "bg-ember-600/60 cursor-not-allowed" : "bg-ember-600 hover:bg-ember-700",
          )}
        >
          <Plus className="w-3.5 h-3.5" /> Add
        </button>
      </div>
    </div>
  );
}
