import { useEffect, useReducer, useState } from "react"
import { computeApi } from "@/lib/api"
import { toast } from "sonner"
import {
    Save,
    RotateCcw,
    Settings2,
    Cpu,
    Database,
    Layers,
    Server,
    Cloud,
    Terminal,
    ShieldCheck,
    Zap,
    ChevronDown,
    ChevronRight
} from "lucide-react"
import { cn } from "@/lib/utils"
import { LazyMotion, domAnimation, m, AnimatePresence } from "framer-motion"

interface DeploymentData {
    id?: string
    deployment_id?: string
    model_name?: string
    engine?: string
    workload_type?: string
    model_type?: string
    replicas?: number
    inference_model?: string
    endpoint?: string
    configuration?: any
}

interface DeploymentConfigProps {
    deployment: DeploymentData
    onUpdate?: () => void
}

type State = {
    loading: boolean;
    config: any;
    replicas: number;
    inferenceModel: string;
    vllmImage: string;
    maxModelLen: string;
    gpuUtil: string;
    gitRepo: string;
    trainingScript: string;
    datasetUrl: string;
    hfToken: string;
    // Advanced VLLM config
    dtype: string;
    enforceEager: boolean;
    maxNumSeqs: string;
    enableChunkedPrefill: boolean;
    kvCacheDtype: string;
    trustRemoteCode: boolean;
    cudaModuleLoading: string;
    nvidiaDisableCudaCompat: string;
    quantization: string;
    // Embedding config
    port: string;
    batchSize: string;
    maxBatchTokens: string;
    pooling: string;
    requiredCpu: string;
    requiredRam: string;
    gpuEnabled: boolean;
};

type Action =
    | { type: 'SET_LOADING'; payload: boolean }
    | { type: 'SET_FIELD'; field: keyof State; value: any }
    | { type: 'INIT_CONFIG'; payload: Partial<State> };

const initialState = (deployment: DeploymentData): State => ({
    loading: false,
    config: {},
    replicas: deployment?.replicas || 1,
    inferenceModel: deployment?.inference_model || "",
    vllmImage: "",
    maxModelLen: "",
    gpuUtil: "",
    gitRepo: "",
    trainingScript: "",
    datasetUrl: "",
    hfToken: "",
    // Advanced VLLM defaults (matching backend defaults)
    dtype: "auto",
    enforceEager: true,
    maxNumSeqs: "256",
    enableChunkedPrefill: true,
    kvCacheDtype: "auto",
    trustRemoteCode: true,
    cudaModuleLoading: "LAZY",
    nvidiaDisableCudaCompat: "1",
    quantization: "",
    // Advanced Embedding defaults
    port: "8080",
    batchSize: "32",
    maxBatchTokens: "16384",
    pooling: "cls",
    requiredCpu: "2",
    requiredRam: "4096",
    gpuEnabled: false,
});

function reducer(state: State, action: Action): State {
    switch (action.type) {
        case 'SET_LOADING':
            return { ...state, loading: action.payload };
        case 'SET_FIELD':
            return { ...state, [action.field]: action.value };
        case 'INIT_CONFIG':
            return { ...state, ...action.payload };
        default:
            return state;
    }
}

export default function DeploymentConfig({ deployment, onUpdate }: DeploymentConfigProps) {
    const [state, dispatch] = useReducer(reducer, deployment, initialState);
    const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
    const {
        loading, config, replicas, inferenceModel, vllmImage, maxModelLen, gpuUtil,
        gitRepo, trainingScript, datasetUrl, hfToken,
        dtype, enforceEager, maxNumSeqs, enableChunkedPrefill, kvCacheDtype, trustRemoteCode, cudaModuleLoading, nvidiaDisableCudaCompat, quantization
    } = state;

    const isTraining = deployment?.workload_type === "training"
    const isVllm = deployment?.engine === "vllm"
    const isEmbedding = deployment?.engine === "tei" || deployment?.engine === "infinity"

    useEffect(() => {
        if (deployment?.configuration) {
            const c = deployment.configuration
            const updates: Partial<State> = { config: c };
            if (isVllm) {
                updates.vllmImage = c.image || "vllm/vllm-openai:latest";
                if (c.cmd) {
                    const mLenIdx = c.cmd.indexOf("--max-model-len")
                    if (mLenIdx !== -1) updates.maxModelLen = c.cmd[mLenIdx + 1]
                    const gUtilIdx = c.cmd.indexOf("--gpu-memory-utilization")
                    if (gUtilIdx !== -1) updates.gpuUtil = c.cmd[gUtilIdx + 1]
                }
                if (c.env?.HF_TOKEN) updates.hfToken = c.env.HF_TOKEN
                // Parse advanced config from metadata
                updates.dtype = c.dtype || "auto";
                updates.enforceEager = c.enforce_eager ?? true;
                updates.maxNumSeqs = String(c.max_num_seqs || 256);
                updates.enableChunkedPrefill = c.enable_chunked_prefill ?? true;
                updates.kvCacheDtype = c.kv_cache_dtype || "auto";
                updates.trustRemoteCode = c.trust_remote_code ?? true;
                updates.cudaModuleLoading = c.cuda_module_loading || "LAZY";
                updates.nvidiaDisableCudaCompat = c.nvidia_disable_cuda_compat || "1";
                updates.quantization = c.quantization || "";
            }
            if (isEmbedding) {
                updates.port = String(c.port || (deployment.engine === "infinity" ? 7997 : 8080));
                updates.batchSize = String(c.batch_size || 32);
                updates.maxBatchTokens = String(c.max_batch_tokens || 16384);
                updates.pooling = c.pooling || "cls";
                updates.requiredCpu = String(c.required_cpu || 2);
                updates.requiredRam = String(c.required_ram || 4096);
                updates.gpuEnabled = c.gpu ?? false;
            }
            if (isTraining) {
                updates.gitRepo = c.git_repo || "";
                updates.trainingScript = c.training_script || "";
                updates.datasetUrl = c.dataset_url || "";
                updates.hf_token = c.hf_token || "";
            }
            dispatch({ type: 'INIT_CONFIG', payload: updates });
        }
    }, [deployment, isVllm, isTraining, isEmbedding])

    const handleSave = async () => {
        dispatch({ type: 'SET_LOADING', payload: true });
        try {
            let updatedConfig = { ...config }
            if (isVllm) {
                updatedConfig.image = vllmImage
                if (updatedConfig.cmd) {
                    // Helper to update or append flag with value
                    const updateFlag = (flag: string, value: string) => {
                        const idx = updatedConfig.cmd.indexOf(flag)
                        if (idx !== -1) updatedConfig.cmd[idx + 1] = value
                        else updatedConfig.cmd.push(flag, value)
                    }
                    // Helper to toggle boolean flag
                    const toggleFlag = (flag: string, enabled: boolean) => {
                        const idx = updatedConfig.cmd.indexOf(flag)
                        if (enabled && idx === -1) updatedConfig.cmd.push(flag)
                        else if (!enabled && idx !== -1) updatedConfig.cmd.splice(idx, 1)
                    }

                    updateFlag("--max-model-len", maxModelLen || "8192")
                    updateFlag("--gpu-memory-utilization", gpuUtil || "0.95")
                    updateFlag("--max-num-seqs", maxNumSeqs || "256")
                    updateFlag("--dtype", dtype || "auto")
                    updateFlag("--kv-cache-dtype", kvCacheDtype || "auto")

                    if (quantization) updateFlag("--quantization", quantization)

                    toggleFlag("--trust-remote-code", trustRemoteCode)
                    toggleFlag("--enforce-eager", enforceEager)
                    toggleFlag("--enable-chunked-prefill", enableChunkedPrefill)
                }

                if (hfToken) {
                    updatedConfig.env = { ...updatedConfig.env, HF_TOKEN: hfToken }
                }

                if (cudaModuleLoading || nvidiaDisableCudaCompat) {
                    updatedConfig.env = {
                        ...updatedConfig.env,
                        ...(cudaModuleLoading ? { CUDA_MODULE_LOADING: cudaModuleLoading } : {}),
                        ...(nvidiaDisableCudaCompat ? { NVIDIA_DISABLE_CUDA_COMPAT: nvidiaDisableCudaCompat } : {})
                    }
                }

                // Save advanced config as metadata
                updatedConfig.dtype = dtype;
                updatedConfig.enforce_eager = enforceEager;
                updatedConfig.max_num_seqs = parseInt(maxNumSeqs) || 256;
                updatedConfig.enable_chunked_prefill = enableChunkedPrefill;
                updatedConfig.kv_cache_dtype = kvCacheDtype;
                updatedConfig.trust_remote_code = trustRemoteCode;
                updatedConfig.cuda_module_loading = cudaModuleLoading || null;
                updatedConfig.nvidia_disable_cuda_compat = nvidiaDisableCudaCompat || null;
                updatedConfig.quantization = quantization || null;
            }
            if (isEmbedding) {
                updatedConfig.port = parseInt(port) || 8080;
                updatedConfig.batch_size = parseInt(batchSize) || 32;
                updatedConfig.max_batch_tokens = parseInt(maxBatchTokens) || 16384;
                updatedConfig.pooling = pooling;
                updatedConfig.required_cpu = parseInt(requiredCpu) || 2;
                updatedConfig.required_ram = parseInt(requiredRam) || 4096;
                updatedConfig.gpu = gpuEnabled;
            }
            if (isTraining) {
                updatedConfig.git_repo = gitRepo
                updatedConfig.training_script = trainingScript
                updatedConfig.dataset_url = datasetUrl
                updatedConfig.hf_token = hfToken
            }
            const payload: any = { configuration: updatedConfig, replicas: replicas, inference_model: inferenceModel || undefined }
            if (isEmbedding) {
                payload.inference_model = updatedConfig.model_id || inferenceModel;
            }
            await computeApi.patch(`/deployment/update/${deployment.id || deployment.deployment_id}`, payload)
            toast.success("Configuration updated successfully")
            if (onUpdate) onUpdate()
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to update configuration")
        } finally {
            dispatch({ type: 'SET_LOADING', payload: false });
        }
    }

    return (
        <LazyMotion features={domAnimation}>
            <m.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
                <Header loading={loading} onSave={handleSave} />
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div className="lg:col-span-2 space-y-6">
                        <GeneralSettings replicas={replicas} inferenceModel={inferenceModel} dispatch={dispatch} />
                        <AnimatePresence mode="wait">
                            {isVllm && <VllmSettings
                                vllmImage={vllmImage}
                                maxModelLen={maxModelLen}
                                gpuUtil={gpuUtil}
                                dtype={dtype}
                                enforceEager={enforceEager}
                                maxNumSeqs={maxNumSeqs}
                                enableChunkedPrefill={enableChunkedPrefill}
                                kvCacheDtype={kvCacheDtype}
                                trustRemoteCode={trustRemoteCode}
                                cudaModuleLoading={cudaModuleLoading}
                                nvidiaDisableCudaCompat={nvidiaDisableCudaCompat}
                                quantization={quantization}
                                isAdvancedOpen={isAdvancedOpen}
                                setIsAdvancedOpen={setIsAdvancedOpen}
                                dispatch={dispatch}
                            />}
                            {isEmbedding && <EmbeddingSettings
                                engine={deployment?.engine || ""}
                                port={port}
                                batchSize={batchSize}
                                maxBatchTokens={maxBatchTokens}
                                pooling={pooling}
                                requiredCpu={requiredCpu}
                                requiredRam={requiredRam}
                                gpuEnabled={gpuEnabled}
                                isAdvancedOpen={isAdvancedOpen}
                                setIsAdvancedOpen={setIsAdvancedOpen}
                                dispatch={dispatch}
                            />}
                            {isTraining && <TrainingSettings gitRepo={gitRepo} trainingScript={trainingScript} datasetUrl={datasetUrl} dispatch={dispatch} />}
                        </AnimatePresence>
                    </div>
                    <div className="space-y-6">
                        <EnvironmentSecrets hfToken={hfToken} dispatch={dispatch} />
                        <ProTip />
                    </div>
                </div>
            </m.div>
        </LazyMotion>
    )
}

function Header({ loading, onSave }: { loading: boolean; onSave: () => void }) {
    return (
        <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
                <div className="p-2 bg-emerald-500/10 rounded-lg"><Settings2 className="w-5 h-5 text-emerald-500" /></div>
                <div><h2 className="text-xl font-bold tracking-tight">Deployment Configuration</h2><p className="text-sm text-muted-foreground font-mono">Customize engine parameters and runtime settings</p></div>
            </div>
            <div className="flex items-center gap-2">
                <button onClick={() => window.location.reload()} className="p-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-all"><RotateCcw className="w-5 h-5" /></button>
                <button onClick={onSave} disabled={loading} className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-emerald-500/20 active:scale-95 disabled:opacity-50">{loading ? <Zap className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save Changes</button>
            </div>
        </div>
    );
}

function GeneralSettings({ replicas, inferenceModel, dispatch }: { replicas: number; inferenceModel: string; dispatch: React.Dispatch<Action> }) {
    return (
        <div className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-emerald-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Server className="w-4 h-4 text-emerald-500 dark:text-emerald-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">General Settings</h3></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-2"><label htmlFor="replicas" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Replicas</label><div className="relative group/input"><input id="replicas" type="number" min="1" value={replicas} onChange={e => dispatch({ type: 'SET_FIELD', field: 'replicas', value: parseInt(e.target.value) })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /><div className="absolute right-3 top-3.5 text-muted-foreground pointer-events-none group-focus-within/input:text-emerald-500"><Layers className="w-4 h-4" /></div></div></div>
                <div className="space-y-2"><label htmlFor="inference-model" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Inference Model</label><div className="relative group/input"><input id="inference-model" value={inferenceModel} onChange={e => dispatch({ type: 'SET_FIELD', field: 'inferenceModel', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" placeholder="e.g. meta-llama/Llama-3-8B" /><div className="absolute right-3 top-3.5 text-muted-foreground pointer-events-none group-focus-within/input:text-emerald-500"><Cloud className="w-4 h-4" /></div></div></div>
            </div>
        </div>
    );
}

function VllmSettings({
    vllmImage, maxModelLen, gpuUtil,
    dtype, enforceEager, maxNumSeqs, enableChunkedPrefill, kvCacheDtype, trustRemoteCode, cudaModuleLoading, nvidiaDisableCudaCompat, quantization,
    isAdvancedOpen, setIsAdvancedOpen, dispatch
}: {
    vllmImage: string; maxModelLen: string; gpuUtil: string;
    dtype: string; enforceEager: boolean; maxNumSeqs: string;
    enableChunkedPrefill: boolean; kvCacheDtype: string; trustRemoteCode: boolean;
    cudaModuleLoading: string; nvidiaDisableCudaCompat: string; quantization: string;
    isAdvancedOpen: boolean; setIsAdvancedOpen: (open: boolean) => void;
    dispatch: React.Dispatch<Action>
}) {
    return (
        <m.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-emerald-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Zap className="w-4 h-4 text-emerald-500 dark:text-emerald-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">vLLM Optimization</h3></div>
            <div className="space-y-6">
                <div className="space-y-2"><label htmlFor="vllm-image" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Container Image</label><input id="vllm-image" value={vllmImage} onChange={e => dispatch({ type: 'SET_FIELD', field: 'vllmImage', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" /></div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-2"><label htmlFor="max-model-len" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Max Model Length</label><input id="max-model-len" value={maxModelLen} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
                    <div className="space-y-2"><label htmlFor="gpu-util" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">GPU Util</label><input id="gpu-util" value={gpuUtil} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuUtil', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
                </div>

                {/* Advanced Config Section */}
                <div className="border-t border-border pt-4">
                    <button
                        onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
                        className="flex items-center gap-2 text-xs font-bold text-muted-foreground uppercase tracking-wider hover:text-emerald-500 transition-colors"
                    >
                        {isAdvancedOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                        Advanced Configuration
                    </button>

                    {isAdvancedOpen && (
                        <m.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4"
                        >
                            <div className="space-y-2">
                                <label htmlFor="dtype" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Data Type (dtype)</label>
                                <select id="dtype" value={dtype} onChange={e => dispatch({ type: 'SET_FIELD', field: 'dtype', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm">
                                    <option value="auto">auto</option>
                                    <option value="float16">float16</option>
                                    <option value="bfloat16">bfloat16</option>
                                    <option value="float32">float32</option>
                                </select>
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="kv-cache-dtype" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">KV Cache dtype</label>
                                <select id="kv-cache-dtype" value={kvCacheDtype} onChange={e => dispatch({ type: 'SET_FIELD', field: 'kvCacheDtype', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm">
                                    <option value="auto">auto</option>
                                    <option value="fp8">fp8</option>
                                    <option value="fp8_e4m3">fp8_e4m3</option>
                                    <option value="fp8_e5m2">fp8_e5m2</option>
                                </select>
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="max-num-seqs" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Max Num Sequences</label>
                                <input id="max-num-seqs" type="number" min="1" value={maxNumSeqs} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxNumSeqs', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" />
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="quantization" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Quantization</label>
                                <select id="quantization" value={quantization} onChange={e => dispatch({ type: 'SET_FIELD', field: 'quantization', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm">
                                    <option value="">None</option>
                                    <option value="awq">AWQ</option>
                                    <option value="gptq">GPTQ</option>
                                    <option value="squeezellm">SqueezeLLM</option>
                                </select>
                            </div>

                            <div className="flex items-center gap-3">
                                <input
                                    id="trust-remote-code"
                                    type="checkbox"
                                    checked={trustRemoteCode}
                                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'trustRemoteCode', value: e.target.checked })}
                                    className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                                />
                                <label htmlFor="trust-remote-code" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">Trust Remote Code</label>
                            </div>

                            <div className="flex items-center gap-3">
                                <input
                                    id="enforce-eager"
                                    type="checkbox"
                                    checked={enforceEager}
                                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'enforceEager', value: e.target.checked })}
                                    className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                                />
                                <label htmlFor="enforce-eager" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">Enforce Eager Mode</label>
                            </div>

                            <div className="flex items-center gap-3">
                                <input
                                    id="enable-chunked-prefill"
                                    type="checkbox"
                                    checked={enableChunkedPrefill}
                                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'enableChunkedPrefill', value: e.target.checked })}
                                    className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                                />
                                <label htmlFor="enable-chunked-prefill" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">Enable Chunked Prefill</label>
                            </div>

                            <div className="flex items-center gap-3">
                                <input
                                    id="cuda-module-loading"
                                    type="checkbox"
                                    checked={!!cudaModuleLoading}
                                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'cudaModuleLoading', value: e.target.checked ? "LAZY" : "" })}
                                    className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                                />
                                <label htmlFor="cuda-module-loading" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">CUDA Module Loading: LAZY</label>
                            </div>

                            <div className="flex items-center gap-3">
                                <input
                                    id="nvidia-disable-cuda-compat"
                                    type="checkbox"
                                    checked={!!nvidiaDisableCudaCompat}
                                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'nvidiaDisableCudaCompat', value: e.target.checked ? "1" : "" })}
                                    className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                                />
                                <label htmlFor="nvidia-disable-cuda-compat" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">NVIDIA Disable CUDA Compat</label>
                            </div>
                        </m.div>
                    )}
                </div>
            </div>
        </m.div>
    );
}

function EmbeddingSettings({
    engine, port, batchSize, maxBatchTokens, pooling, requiredCpu, requiredRam, gpuEnabled,
    isAdvancedOpen, setIsAdvancedOpen, dispatch
}: {
    engine: string; port: string; batchSize: string; maxBatchTokens: string; pooling: string;
    requiredCpu: string; requiredRam: string; gpuEnabled: boolean;
    isAdvancedOpen: boolean; setIsAdvancedOpen: (open: boolean) => void;
    dispatch: React.Dispatch<Action>
}) {
    return (
        <m.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-emerald-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Database className="w-4 h-4 text-emerald-500 dark:text-emerald-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">Embedding Optimization</h3></div>
            <div className="space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {engine === "infinity" && (
                        <div className="space-y-2"><label htmlFor="batch-size" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Batch Size</label><input id="batch-size" type="number" value={batchSize} onChange={e => dispatch({ type: 'SET_FIELD', field: 'batchSize', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
                    )}
                    {engine === "tei" && (
                        <div className="space-y-2"><label htmlFor="max-batch-tokens" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Max Batch Tokens</label><input id="max-batch-tokens" type="number" value={maxBatchTokens} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxBatchTokens', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
                    )}
                    <div className="flex items-center gap-3 pt-6">
                        <input
                            id="gpu-enabled"
                            type="checkbox"
                            checked={gpuEnabled}
                            onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuEnabled', value: e.target.checked })}
                            className="w-4 h-4 rounded border-border bg-background text-emerald-500 focus:ring-emerald-500/40"
                        />
                        <label htmlFor="gpu-enabled" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter">Enable GPU Acceleration</label>
                    </div>
                </div>

                <div className="border-t border-border pt-4">
                    <button
                        onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
                        className="flex items-center gap-2 text-xs font-bold text-muted-foreground uppercase tracking-wider hover:text-emerald-500 transition-colors"
                    >
                        {isAdvancedOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                        Advanced Hardware Configuration
                    </button>

                    {isAdvancedOpen && (
                        <m.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4"
                        >
                            <div className="space-y-2">
                                <label htmlFor="req-cpu" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">CPU Cores</label>
                                <input id="req-cpu" type="number" value={requiredCpu} onChange={e => dispatch({ type: 'SET_FIELD', field: 'requiredCpu', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" />
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="req-ram" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">RAM (MB)</label>
                                <input id="req-ram" type="number" value={requiredRam} onChange={e => dispatch({ type: 'SET_FIELD', field: 'requiredRam', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" />
                            </div>

                            {engine === "tei" && (
                                <div className="space-y-2">
                                    <label htmlFor="pooling-strategy" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Pooling Strategy</label>
                                    <select id="pooling-strategy" value={pooling} onChange={e => dispatch({ type: 'SET_FIELD', field: 'pooling', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm">
                                        <option value="cls">CLS</option>
                                        <option value="mean">Mean</option>
                                        <option value="last_token">Last Token</option>
                                    </select>
                                </div>
                            )}

                            <div className="space-y-2">
                                <label htmlFor="port" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Service Port</label>
                                <input id="port" type="number" value={port} onChange={e => dispatch({ type: 'SET_FIELD', field: 'port', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" />
                            </div>
                        </m.div>
                    )}
                </div>
            </div>
        </m.div>
    );
}

function TrainingSettings({ gitRepo, trainingScript, datasetUrl, dispatch }: { gitRepo: string; trainingScript: string; datasetUrl: string; dispatch: React.Dispatch<Action> }) {
    return (
        <m.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-amber-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Terminal className="w-4 h-4 text-amber-500 focus:text-amber-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">Training Orchestration</h3></div>
            <div className="space-y-6">
                <div className="space-y-2"><label htmlFor="git-repo" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Git Repository</label><input id="git-repo" value={gitRepo} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gitRepo', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm" placeholder="https://..." /></div>
                <div className="space-y-2"><label htmlFor="training-script" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Training Command</label><textarea id="training-script" rows={3} value={trainingScript} onChange={e => dispatch({ type: 'SET_FIELD', field: 'trainingScript', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm resize-none" /></div>
                <div className="space-y-2"><label htmlFor="dataset-url" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Dataset URL</label><input id="dataset-url" value={datasetUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'datasetUrl', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm" /></div>
            </div>
        </m.div>
    );
}

function EnvironmentSecrets({ hfToken, dispatch }: { hfToken: string; dispatch: React.Dispatch<Action> }) {
    return (
        <div className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-6 text-foreground"><ShieldCheck className="w-4 h-4 text-purple-600 dark:text-purple-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">Environment Secrets</h3></div>
            <div className="space-y-2"><label htmlFor="hf-token" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Hugging Face Token</label><input id="hf-token" type="password" value={hfToken} onChange={e => dispatch({ type: 'SET_FIELD', field: 'hfToken', value: e.target.value })} className="w-full bg-white dark:bg-zinc-900 border border-border rounded-xl px-4 py-3 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-purple-500/40 font-mono text-sm" placeholder="hf_••••••••" /><p className="text-[10px] text-muted-foreground font-mono mt-2">Required for gated models.</p></div>
        </div>
    );
}

function ProTip() {
    return (
        <div className="bg-emerald-600/10 border border-emerald-500/20 rounded-2xl p-6">
            <h4 className="text-sm font-bold text-emerald-600 dark:text-emerald-400 mb-2">Pro Tip</h4>
            <p className="text-xs text-emerald-600/70 dark:text-emerald-300/70 leading-relaxed font-mono">Wait for <span className="text-emerald-600 dark:text-emerald-400 underline">STOPPED</span> state before applying major changes.</p>
        </div>
    );
}
