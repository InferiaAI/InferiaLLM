import {
  Cpu, Database, Image, Video, Layers, Terminal, Zap,
  AlertCircle, Loader2, ChevronDown, ChevronRight,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { getFitColor } from "@/services/modelPlanner"
import { CompatibilityProjectionChart } from "@/components/deployment/CompatibilityProjectionChart"
import type { State, Action } from "@/pages/NewDeployment"

/**
 * Per-engine / per-model-type configuration sections for the managed deploy
 * wizard's final step.
 *
 * Each section is responsible for rendering ONLY when its engine/model-type is
 * active — the caller (`ManagedConfig`) gates them so that, e.g., the vLLM
 * configuration never spills onto the page when Inferia Diffusion or vLLM Omni
 * is selected. Keeping the gate at the call site (rather than a `return null`
 * inside each component) keeps the wizard's render tree readable.
 */

type SectionProps = {
  state: State
  dispatch: React.Dispatch<Action>
}

// Engine AMI dropdown for AWS-only sites that bake a vLLM engine AMI.
interface EngineAmi { ami_id: string; vllm_tag?: string }

/** Shared HuggingFace token dropdown (vLLM, SGLang, diffusion, omni). */
export function HfTokenSelect({
  id,
  value,
  dispatch,
  hfTokenNames,
}: {
  id: string
  value: string
  dispatch: React.Dispatch<Action>
  hfTokenNames: string[]
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-muted-foreground mb-1.5">
        HuggingFace Token <span className="text-muted-foreground/60">(optional — required for gated models)</span>
      </label>
      <select
        id={id}
        value={value}
        onChange={e => dispatch({ type: 'SET_FIELD', field: 'selectedHfTokenName', value: e.target.value })}
        className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white"
      >
        <option value="">None</option>
        {hfTokenNames.map(name => (
          <option key={name} value={name}>{name}</option>
        ))}
      </select>
      {hfTokenNames.length === 0 && (
        <p className="text-xs text-muted-foreground mt-1">
          No saved tokens — add one at Settings → Providers → HuggingFace.
        </p>
      )}
    </div>
  )
}

/** Engine AMI dropdown — only rendered for vLLM deploys onto AWS pools. */
export function EngineAmiSelect({
  selectedAmiId,
  dispatch,
  engineAmis,
  amisLoading,
  amiRegion,
}: {
  selectedAmiId: string
  dispatch: React.Dispatch<Action>
  engineAmis: EngineAmi[]
  amisLoading: boolean
  amiRegion: string
}) {
  return (
    <div>
      <label htmlFor="engineAmi" className="block text-xs font-medium text-muted-foreground mb-1.5">
        Engine AMI <span className="text-rose-500">*</span>
      </label>
      {amisLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading AMIs for {amiRegion}…
        </div>
      ) : engineAmis.length === 0 ? (
        <div className="flex items-center gap-2 p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md text-xs text-amber-700 dark:text-amber-300">
          <AlertCircle className="w-4 h-4 shrink-0" />
          No engine AMIs in {amiRegion} — bake one first (Settings → Providers → AWS).
        </div>
      ) : (
        <select
          id="engineAmi"
          value={selectedAmiId}
          onChange={e => dispatch({ type: 'SET_FIELD', field: 'selectedAmiId', value: e.target.value })}
          className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white"
        >
          <option value="">— select an AMI —</option>
          {engineAmis.map(ami => (
            <option key={ami.ami_id} value={ami.ami_id}>
              {ami.ami_id}{ami.vllm_tag ? ` — vLLM ${ami.vllm_tag}` : ""}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

/**
 * Model-planner / compatibility projection. Rendered only when the planner has
 * produced a result — which the caller only requests for vLLM/Ollama, so it is
 * naturally skipped for diffusion / omni / embedding engines.
 */
export function CompatibilityPanel({
  compatibility,
  selectedPool,
  selectedEngine,
  dispatch,
}: {
  compatibility: any
  selectedPool: any
  selectedEngine: string
  dispatch: React.Dispatch<Action>
}) {
  return (
    <div className={cn("mt-4 p-5 rounded-xl border-2 transition-colors animate-in fade-in slide-in-from-top-4", getFitColor(compatibility.fitLevel))}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-current/10">
            <Zap className="w-5 h-5" />
          </div>
          <div>
            <span className="font-bold text-base block tracking-tight underline decoration-current/30 underline-offset-4 decoration-2">Compatibility: {compatibility.fitLevel}</span>
            <span className="text-[10px] uppercase font-semibold opacity-60">Engine Assessment • {compatibility.score}/100</span>
          </div>
        </div>
      </div>

      <p className="text-sm font-medium opacity-90 leading-snug mb-5 decoration-current/20">{compatibility.reason}</p>

      <div className="mb-6">
        <CompatibilityProjectionChart
          compatibility={compatibility}
          poolName={selectedPool?.pool_name}
          inputTokens={200}
          outputTokens={200}
        />
      </div>

      {/* Multi-Dimensional Breakdown */}
      <div className="space-y-3 mb-6">
        {[
          { label: 'Quality', value: compatibility.details.qualityScore, icon: '💎' },
          { label: 'Speed', value: compatibility.details.speedScore, icon: '🏎️' },
          { label: 'Fit', value: compatibility.details.fitScore, icon: '🧩' },
          { label: 'Context', value: compatibility.details.contextScore, icon: '📏' }
        ].map((stat) => (
          <div key={stat.label} className="space-y-1">
            <div className="flex justify-between text-[11px] font-bold uppercase tracking-wider">
              <span className="flex items-center gap-1.5 opacity-80">{stat.icon} {stat.label}</span>
              <span>{Math.round(stat.value)}%</span>
            </div>
            <div className="h-1.5 w-full bg-current/10 rounded-full overflow-hidden">
              <div
                className="h-full bg-current transition-colors duration-1000 ease-out"
                style={{ width: `${stat.value}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6 pt-5 border-t border-current/15">
        <div className="bg-current/5 p-3 rounded-lg border border-current/10">
          <div className="text-[10px] uppercase font-black tracking-widest opacity-50 mb-1">Est. Throughput (Single GPU)</div>
          <div className="text-lg font-black">{compatibility.estimatedTps.toFixed(1)} <span className="text-xs font-normal opacity-70">tokens/s (estimated)</span></div>
        </div>
        <div className="bg-current/5 p-3 rounded-lg border border-current/10">
          <div className="text-[10px] uppercase font-black tracking-widest opacity-50 mb-1">VRAM Allocation</div>
          <div className="text-lg font-black">{compatibility.requiredVram.toFixed(1)} <span className="text-xs font-normal opacity-70">/ {compatibility.availableVram} GB</span></div>
        </div>
        <div className="bg-current/5 p-3 rounded-lg border border-current/10">
          <div className="text-[10px] uppercase font-black tracking-widest opacity-50 mb-1">Max Context Length</div>
          <div className="text-lg font-black">{compatibility.contextLength ? `${compatibility.contextLength.toLocaleString()} tokens` : "Unknown"}</div>
        </div>
        <div className="bg-current/5 p-3 rounded-lg border border-current/10">
          <div className="text-[10px] uppercase font-black tracking-widest opacity-50 mb-1">Recommended Quant</div>
          <div className="text-lg font-black uppercase">{compatibility.bestQuant || "Auto (Native)"}</div>
        </div>
      </div>

      {compatibility.recommendedVllmConfig && selectedEngine === 'vllm' && (
        <div className="mt-5 pt-4 border-t border-current/15">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[10px] uppercase font-black tracking-widest opacity-60 flex items-center gap-1.5">
              <Terminal className="w-3 h-3" /> Recommended vLLM Settings
            </span>
            <button
              type="button"
              onClick={() => {
                const cfg = compatibility.recommendedVllmConfig!;
                const optimalMaxLen = compatibility.contextLength || cfg.maxModelLen;
                dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: optimalMaxLen.toString() });
                dispatch({ type: 'SET_FIELD', field: 'gpuUtil', value: cfg.gpuMemoryUtilization.toString() });
                dispatch({ type: 'SET_FIELD', field: 'enforceEager', value: cfg.enforceEager });
                dispatch({ type: 'SET_FIELD', field: 'dtype', value: cfg.dtype });
                toast.success("Applied model optimizations and context limits.");
              }}
              className="px-3 py-1 bg-current/10 hover:bg-current/20 rounded-md text-[10px] font-black uppercase transition-colors border border-current/20 active:scale-95"
            >
              Apply Settings
            </button>
          </div>
          <div className="grid grid-cols-2 gap-y-2 text-[10px] font-medium opacity-80">
            <div className="flex justify-between pr-4"><span>Max Length:</span> <span>{compatibility.recommendedVllmConfig.maxModelLen}</span></div>
            <div className="flex justify-between pl-4 border-l border-current/10"><span>GPU Util:</span> <span>{compatibility.recommendedVllmConfig.gpuMemoryUtilization}</span></div>
            <div className="flex justify-between pr-4"><span>Eager Mode:</span> <span>{compatibility.recommendedVllmConfig.enforceEager ? 'Yes' : 'No'}</span></div>
            <div className="flex justify-between pl-4 border-l border-current/10"><span>DType:</span> <span className="uppercase">{compatibility.recommendedVllmConfig.dtype}</span></div>
          </div>
        </div>
      )}

      {compatibility.fitLevel === "TooTight" && (
        <div className="mt-4 p-3 bg-rose-500/15 rounded-lg border-2 border-rose-500/30 text-xs font-bold flex items-start gap-3 text-rose-600 dark:text-rose-400">
          <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
          <span>Critical: Model memory exceeds pool capacity. Deployment will likely fail or cause Hardware OOM.</span>
        </div>
      )}
    </div>
  )
}

/** Auto-replica scale-out config (inference deploys). */
export function AutoReplicaConfig({ state, dispatch }: SectionProps) {
  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2">
        <Zap className="w-4 h-4 text-amber-500" />
        <h4 className="font-medium text-sm">Auto-Replica</h4>
      </div>
      <div className="flex items-center gap-3 mb-3">
        <input
          id="autoReplicaEnabled"
          type="checkbox"
          checked={state.autoReplicaEnabled}
          onChange={e => dispatch({ type: 'SET_FIELD', field: 'autoReplicaEnabled', value: e.target.checked })}
          className="w-4 h-4 rounded border-border"
        />
        <label htmlFor="autoReplicaEnabled" className="text-xs font-medium text-muted-foreground">
          Automatically provision new nodes when throughput degrades
        </label>
      </div>
      {state.autoReplicaEnabled && (
        <div>
          <label htmlFor="tokensPerSecondThreshold" className="block text-xs font-medium text-muted-foreground mb-1.5">
            Tokens/sec threshold <span className="text-muted-foreground/60">(scale out when average drops below)</span>
          </label>
          <div className="relative">
            <input
              id="tokensPerSecondThreshold"
              type="number"
              min="0.1"
              step="0.1"
              value={state.tokensPerSecondThreshold}
              onChange={e => dispatch({ type: 'SET_FIELD', field: 'tokensPerSecondThreshold', value: e.target.value })}
              className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white pr-12"
              placeholder="10"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">tok/s</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1.5">
            Monitors average tokens/sec over 5-minute windows. Provisions a new pool node when the threshold is breached.
          </p>
        </div>
      )}
    </div>
  )
}

/** GPU-per-replica slider + prefill/decode split (multi-GPU vLLM/SGLang pools). */
export function GpuSplitConfig({ state, dispatch, selectedPool }: SectionProps & { selectedPool: any }) {
  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2">
        <Cpu className="w-4 h-4 text-primary" />
        <h4 className="font-medium text-sm">GPU Configuration</h4>
      </div>
      <div>
        <label htmlFor="gpuPerReplica" className="block text-xs font-medium text-muted-foreground mb-2">
          GPUs per Replica: <span className="font-bold text-foreground">{state.gpuPerReplica || "1"}</span>
          {parseInt(state.gpuPerReplica || "1") > 1 && (
            <span className="ml-2 text-amber-600 dark:text-amber-400">→ Prefill-Decode split enabled</span>
          )}
        </label>
        <input
          id="gpuPerReplica"
          type="range"
          min="1"
          max={selectedPool.gpu_count}
          value={state.gpuPerReplica}
          onChange={e => {
            const val = e.target.value;
            const gpuCount = parseInt(val);
            dispatch({ type: 'SET_FIELD', field: 'gpuPerReplica', value: val });
            if (gpuCount > 1) {
              const mid = Math.floor(gpuCount / 2);
              const prefillIndices = Array.from({ length: mid }, (_, i) => i).join(",");
              const decodeIndices = Array.from({ length: gpuCount - mid }, (_, i) => i + mid).join(",");
              dispatch({ type: 'SET_FIELD', field: 'prefillReplicas', value: "1" });
              dispatch({ type: 'SET_FIELD', field: 'decodeReplicas', value: "1" });
              dispatch({ type: 'SET_FIELD', field: 'prefillGpuIndices', value: prefillIndices });
              dispatch({ type: 'SET_FIELD', field: 'decodeGpuIndices', value: decodeIndices });
            } else {
              dispatch({ type: 'SET_FIELD', field: 'prefillReplicas', value: "0" });
              dispatch({ type: 'SET_FIELD', field: 'decodeReplicas', value: "0" });
              dispatch({ type: 'SET_FIELD', field: 'prefillGpuIndices', value: "" });
              dispatch({ type: 'SET_FIELD', field: 'decodeGpuIndices', value: "" });
            }
          }}
          className="w-full"
        />
        <div className="flex justify-between text-xs text-muted-foreground mt-1">
          <span>1</span>
          <span>{selectedPool.gpu_count} GPUs</span>
        </div>
      </div>
      {parseInt(state.gpuPerReplica || "1") > 1 && (
        <div className="p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md text-xs text-amber-700 dark:text-amber-300">
          GPUs {state.prefillGpuIndices} → Prefill &nbsp;|&nbsp; GPUs {state.decodeGpuIndices} → Decode
        </div>
      )}
    </div>
  )
}

/** vLLM / SGLang configuration: AMI, HF token, runtime knobs, disagg split. */
export function VllmConfig({
  state,
  dispatch,
  engineAmis,
  amisLoading,
  amiRegion,
  hfTokenNames,
}: SectionProps & {
  engineAmis: EngineAmi[]
  amisLoading: boolean
  amiRegion: string
  hfTokenNames: string[]
}) {
  const {
    selectedEngine, dtype, quantization, maxModelLen, gpuUtil, enforceEager,
    selectedAmiId, selectedHfTokenName,
    prefillReplicas, decodeReplicas, prefillGpuIndices, decodeGpuIndices, isDisaggOpen,
  } = state;

  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2"><Cpu className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">{selectedEngine === "sglang" ? "SGLang" : "vLLM"} Configuration</h4></div>

      {/* Engine AMI dropdown (required for vLLM only) */}
      {selectedEngine === "vllm" && (
        <EngineAmiSelect
          selectedAmiId={selectedAmiId}
          dispatch={dispatch}
          engineAmis={engineAmis}
          amisLoading={amisLoading}
          amiRegion={amiRegion}
        />
      )}

      {/* HF token name dropdown (optional for both) */}
      <HfTokenSelect id="hfTokenName" value={selectedHfTokenName} dispatch={dispatch} hfTokenNames={hfTokenNames} />

      {/* Runtime configuration */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label htmlFor="dtype" className="block text-xs font-medium text-muted-foreground mb-1.5">Data Type</label>
          <select id="dtype" value={dtype} onChange={e => dispatch({ type: 'SET_FIELD', field: 'dtype', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white">
            <option value="auto">auto</option>
            <option value="float16">float16</option>
            <option value="bfloat16">bfloat16</option>
            <option value="float32">float32</option>
          </select>
        </div>
        <div>
          <label htmlFor="quantization" className="block text-xs font-medium text-muted-foreground mb-1.5">Quantization</label>
          <select id="quantization" value={quantization} onChange={e => dispatch({ type: 'SET_FIELD', field: 'quantization', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white">
            <option value="">None</option>
            <option value="fp8">FP8</option>
            <option value="awq">AWQ</option>
            <option value="gptq">GPTQ</option>
          </select>
        </div>
        <div>
          <label htmlFor="maxModelLen" className="block text-xs font-medium text-muted-foreground mb-1.5">Max Model Length</label>
          <input id="maxModelLen" type="number" value={maxModelLen} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="8192" />
        </div>
        <div>
          <label htmlFor="gpuUtil" className="block text-xs font-medium text-muted-foreground mb-1.5">GPU Memory Util</label>
          <input id="gpuUtil" type="number" min="0" max="1" step="0.01" value={gpuUtil} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuUtil', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="0.90" />
        </div>
        <div className="flex items-center gap-2 pt-6">
          <input
            id="enforceEager"
            type="checkbox"
            checked={enforceEager}
            onChange={e => dispatch({ type: 'SET_FIELD', field: 'enforceEager', value: e.target.checked })}
            className="w-4 h-4 rounded border-border"
          />
          <label htmlFor="enforceEager" className="text-xs font-medium text-muted-foreground">Enforce Eager Mode</label>
        </div>
      </div>

      {/* Advanced Configuration: Prefill-Decode Split */}
      <div className="border-t border-border pt-4">
        <button
          type="button"
          onClick={() => dispatch({ type: 'SET_FIELD', field: 'isDisaggOpen', value: !isDisaggOpen })}
          className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-ember-600 dark:hover:text-ember-400 transition-colors"
        >
          {isDisaggOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          Advanced Configuration
        </button>

        {isDisaggOpen && (
          <div className="mt-4 space-y-4">
            <p className="text-xs text-muted-foreground">Configure prefill-decode split for disaggregated deployment across separate GPU sets.</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="prefillReplicas" className="block text-xs font-medium text-muted-foreground mb-1.5">Prefill Replicas</label>
                <input id="prefillReplicas" type="number" min="0" value={prefillReplicas} onChange={e => dispatch({ type: 'SET_FIELD', field: 'prefillReplicas', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="0" />
              </div>
              <div>
                <label htmlFor="decodeReplicas" className="block text-xs font-medium text-muted-foreground mb-1.5">Decode Replicas</label>
                <input id="decodeReplicas" type="number" min="0" value={decodeReplicas} onChange={e => dispatch({ type: 'SET_FIELD', field: 'decodeReplicas', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="0" />
              </div>
              <div>
                <label htmlFor="prefillGpuIndices" className="block text-xs font-medium text-muted-foreground mb-1.5">Prefill GPU Indices</label>
                <input id="prefillGpuIndices" value={prefillGpuIndices} onChange={e => dispatch({ type: 'SET_FIELD', field: 'prefillGpuIndices', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="0,1 (comma-separated)" />
              </div>
              <div>
                <label htmlFor="decodeGpuIndices" className="block text-xs font-medium text-muted-foreground mb-1.5">Decode GPU Indices</label>
                <input id="decodeGpuIndices" value={decodeGpuIndices} onChange={e => dispatch({ type: 'SET_FIELD', field: 'decodeGpuIndices', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" placeholder="2,3 (comma-separated)" />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** Embedding (Infinity / TEI) configuration. */
export function EmbeddingConfig({ state, dispatch }: SectionProps) {
  const {
    selectedEngine, batchSize, maxBatchTokens, gpuEnabled, isAdvancedOpen,
    requiredCpu, requiredRam, pooling,
  } = state;

  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2"><Database className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">Embedding Configuration</h4></div>
      <div className="grid grid-cols-2 gap-4">
        {selectedEngine === "infinity" && (
          <div><label htmlFor="batchSize" className="block text-xs font-medium text-muted-foreground mb-1.5">Batch Size</label><input id="batchSize" type="number" value={batchSize} onChange={e => dispatch({ type: 'SET_FIELD', field: 'batchSize', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" /></div>
        )}
        {selectedEngine === "tei" && (
          <div><label htmlFor="maxBatchTokens" className="block text-xs font-medium text-muted-foreground mb-1.5">Max Batch Tokens</label><input id="maxBatchTokens" type="number" value={maxBatchTokens} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxBatchTokens', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" /></div>
        )}
        <div className="flex items-center gap-2 pt-6">
          <input
            id="gpuEnabled"
            type="checkbox"
            checked={gpuEnabled}
            onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuEnabled', value: e.target.checked })}
            className="w-4 h-4 rounded border-border"
          />
          <label htmlFor="gpuEnabled" className="text-xs font-medium text-muted-foreground">Enable GPU Acceleration</label>
        </div>
      </div>

      <div className="border-t border-border pt-4 mt-4">
        <button
          type="button"
          onClick={() => dispatch({ type: 'SET_FIELD', field: 'isAdvancedOpen', value: !isAdvancedOpen })}
          className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-ember-600 dark:hover:text-ember-400 transition-colors"
        >
          {isAdvancedOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          Advanced Hardware Configuration
        </button>

        {isAdvancedOpen && (
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="requiredCpu" className="block text-xs font-medium text-muted-foreground mb-1.5">Required CPU Cores</label>
              <input id="requiredCpu" type="number" min="1" value={requiredCpu} onChange={e => dispatch({ type: 'SET_FIELD', field: 'requiredCpu', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" />
            </div>
            <div>
              <label htmlFor="requiredRam" className="block text-xs font-medium text-muted-foreground mb-1.5">Required RAM (MB)</label>
              <input id="requiredRam" type="number" min="1024" step="1024" value={requiredRam} onChange={e => dispatch({ type: 'SET_FIELD', field: 'requiredRam', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" />
            </div>
            {selectedEngine === "tei" && (
              <div>
                <label htmlFor="pooling" className="block text-xs font-medium text-muted-foreground mb-1.5">Pooling Strategy</label>
                <select id="pooling" value={pooling} onChange={e => dispatch({ type: 'SET_FIELD', field: 'pooling', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white">
                  <option value="cls">CLS</option>
                  <option value="mean">Mean</option>
                  <option value="last_token">Last Token</option>
                </select>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/** Inferia Diffusion (image/video) configuration. */
export function DiffusionConfig({ state, dispatch, hfTokenNames }: SectionProps & { hfTokenNames: string[] }) {
  const { modelType, selectedHfTokenName } = state;
  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2">
        {modelType === "video_generation" ? <Video className="w-4 h-4 text-primary" /> : <Image className="w-4 h-4 text-primary" />}
        <h4 className="font-medium text-sm">
          {modelType === "video_generation" ? "InferaDiffusion Video Generation" : "InferaDiffusion Image Generation"}
        </h4>
      </div>
      <div className="text-sm text-muted-foreground">
        Model type is automatically set based on your deployment type. API key is configured automatically by the system.
      </div>
      <HfTokenSelect id="hfTokenNameDiff" value={selectedHfTokenName} dispatch={dispatch} hfTokenNames={hfTokenNames} />
      <div className="grid grid-cols-3 gap-4">
        <div className="flex items-center gap-2">
          <input
            id="trustRemoteCode"
            type="checkbox"
            checked={state.trustRemoteCode || false}
            onChange={e => dispatch({ type: 'SET_FIELD', field: 'trustRemoteCode', value: e.target.checked })}
            className="w-4 h-4 rounded border-border text-ember-600 focus:ring-ember-500"
          />
          <label htmlFor="trustRemoteCode" className="text-xs font-medium text-muted-foreground">Trust Remote Code</label>
        </div>
        <div className="flex items-center gap-2">
          <input
            id="modelOffload"
            type="checkbox"
            checked={state.modelOffload || false}
            onChange={e => dispatch({ type: 'SET_FIELD', field: 'modelOffload', value: e.target.checked })}
            className="w-4 h-4 rounded border-border text-ember-600 focus:ring-ember-500"
          />
          <label htmlFor="modelOffload" className="text-xs font-medium text-muted-foreground">Model Offload</label>
        </div>
        <div className="flex items-center gap-2">
          <input
            id="groupOffload"
            type="checkbox"
            checked={state.groupOffload || false}
            onChange={e => dispatch({ type: 'SET_FIELD', field: 'groupOffload', value: e.target.checked })}
            className="w-4 h-4 rounded border-border text-ember-600 focus:ring-ember-500"
          />
          <label htmlFor="groupOffload" className="text-xs font-medium text-muted-foreground">Group Offload</label>
        </div>
      </div>
    </div>
  )
}

/** Inferia vLLM Omni (image/video) configuration. */
export function VllmOmniConfig({ state, dispatch, hfTokenNames }: SectionProps & { hfTokenNames: string[] }) {
  const { modelType, selectedHfTokenName } = state;
  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2">
        {modelType === "video_generation" ? <Video className="w-4 h-4 text-primary" /> : <Image className="w-4 h-4 text-primary" />}
        <h4 className="font-medium text-sm">
          {modelType === "video_generation" ? "vLLM Omni Video Generation" : "vLLM Omni Image Generation"}
        </h4>
      </div>
      <div className="text-sm text-muted-foreground">
        Omni-modal vLLM server. AWS only. Model type is set automatically from your deployment type.
      </div>
      <HfTokenSelect id="hfTokenNameOmni" value={selectedHfTokenName} dispatch={dispatch} hfTokenNames={hfTokenNames} />
      <div className="flex items-center gap-2">
        <input
          id="trustRemoteCodeOmni"
          type="checkbox"
          checked={state.trustRemoteCode || false}
          onChange={e => dispatch({ type: 'SET_FIELD', field: 'trustRemoteCode', value: e.target.checked })}
          className="w-4 h-4 rounded border-border text-ember-600 focus:ring-ember-500"
        />
        <label htmlFor="trustRemoteCodeOmni" className="text-xs font-medium text-muted-foreground">Trust Remote Code</label>
      </div>
    </div>
  )
}

/** Training (git repo / script / dataset) configuration. */
export function TrainingConfig({ state, dispatch }: SectionProps) {
  const { gitRepo, trainingScript, datasetUrl } = state;
  return (
    <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
      <div className="flex items-center gap-2 mb-2"><Layers className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">Training Configuration</h4></div>
      <div><label htmlFor="gitRepo" className="block text-xs font-medium text-muted-foreground mb-1.5">Git Repository URL</label><input id="gitRepo" value={gitRepo} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gitRepo', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" /></div>
      <div><label htmlFor="trainingScript" className="block text-xs font-medium text-muted-foreground mb-1.5">Training Script</label><input id="trainingScript" value={trainingScript} onChange={e => dispatch({ type: 'SET_FIELD', field: 'trainingScript', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md font-mono bg-card dark:text-white" /></div>
      <div><label htmlFor="datasetUrl" className="block text-xs font-medium text-muted-foreground mb-1.5">Dataset URL</label><input id="datasetUrl" value={datasetUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'datasetUrl', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md bg-card dark:text-white" /></div>
    </div>
  )
}

/** Preflight check status banner (checking / failed). */
export function PreflightBanner({
  preflightStatus,
  preflightErrors,
}: {
  preflightStatus: State['preflightStatus']
  preflightErrors: State['preflightErrors']
}) {
  if (preflightStatus === 'checking') {
    return (
      <div className="flex items-center gap-2 p-3 bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-lg text-sm text-blue-700 dark:text-blue-300">
        <Loader2 className="w-4 h-4 animate-spin" /> Running pre-deployment checks...
      </div>
    )
  }
  if (preflightStatus === 'failed' && preflightErrors.length > 0) {
    return (
      <div className="p-4 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg space-y-2">
        <p className="text-sm font-semibold text-red-800 dark:text-red-300">Pre-deployment check failed</p>
        {preflightErrors.map((err, i: number) => (
          <div key={i} className="text-sm text-red-700 dark:text-red-400">
            <p>{err.message}</p>
            {err.needs_hf_token && (
              <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-400">
                Provide a HuggingFace token in the configuration above to access this model.
              </p>
            )}
          </div>
        ))}
      </div>
    )
  }
  return null
}
