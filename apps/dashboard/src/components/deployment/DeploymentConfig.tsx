import { useEffect, useReducer } from "react"
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
    Zap
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
    const {
        loading, config, replicas, inferenceModel, vllmImage, maxModelLen, gpuUtil,
        gitRepo, trainingScript, datasetUrl, hfToken
    } = state;

    const isTraining = deployment?.workload_type === "training"
    const isVllm = deployment?.engine === "vllm"

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
            }
            if (isTraining) {
                updates.gitRepo = c.git_repo || "";
                updates.trainingScript = c.training_script || "";
                updates.datasetUrl = c.dataset_url || "";
                updates.hf_token = c.hf_token || "";
            }
            dispatch({ type: 'INIT_CONFIG', payload: updates });
        }
    }, [deployment, isVllm, isTraining])

    const handleSave = async () => {
        dispatch({ type: 'SET_LOADING', payload: true });
        try {
            let updatedConfig = { ...config }
            if (isVllm) {
                updatedConfig.image = vllmImage
                if (updatedConfig.cmd) {
                    const mLenIdx = updatedConfig.cmd.indexOf("--max-model-len")
                    if (mLenIdx !== -1) updatedConfig.cmd[mLenIdx + 1] = maxModelLen
                    const gUtilIdx = updatedConfig.cmd.indexOf("--gpu-memory-utilization")
                    if (gUtilIdx !== -1) updatedConfig.cmd[gUtilIdx + 1] = gpuUtil
                }
                if (hfToken) updatedConfig.env = { ...updatedConfig.env, HF_TOKEN: hfToken }
            }
            if (isTraining) {
                updatedConfig.git_repo = gitRepo
                updatedConfig.training_script = trainingScript
                updatedConfig.dataset_url = datasetUrl
                updatedConfig.hf_token = hfToken
            }
            const payload: any = { configuration: updatedConfig, replicas: replicas, inference_model: inferenceModel }
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
                            {isVllm && <VllmSettings vllmImage={vllmImage} maxModelLen={maxModelLen} gpuUtil={gpuUtil} dispatch={dispatch} />}
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
                <div className="p-2 bg-blue-500/10 rounded-lg"><Settings2 className="w-5 h-5 text-blue-500" /></div>
                <div><h2 className="text-xl font-bold tracking-tight">Deployment Configuration</h2><p className="text-sm text-muted-foreground font-mono">Customize engine parameters and runtime settings</p></div>
            </div>
            <div className="flex items-center gap-2">
                <button onClick={() => window.location.reload()} className="p-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-all"><RotateCcw className="w-5 h-5" /></button>
                <button onClick={onSave} disabled={loading} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20 active:scale-95 disabled:opacity-50">{loading ? <Zap className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save Changes</button>
            </div>
        </div>
    );
}

function GeneralSettings({ replicas, inferenceModel, dispatch }: { replicas: number; inferenceModel: string; dispatch: React.Dispatch<Action> }) {
    return (
        <div className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-blue-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Server className="w-4 h-4 text-blue-500 dark:text-blue-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">General Settings</h3></div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-2"><label htmlFor="replicas" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Replicas</label><div className="relative group/input"><input id="replicas" type="number" min="1" value={replicas} onChange={e => dispatch({ type: 'SET_FIELD', field: 'replicas', value: parseInt(e.target.value) })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500/40 font-mono" /><div className="absolute right-3 top-3.5 text-muted-foreground pointer-events-none group-focus-within/input:text-blue-500"><Layers className="w-4 h-4" /></div></div></div>
                <div className="space-y-2"><label htmlFor="inference-model" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Inference Model</label><div className="relative group/input"><input id="inference-model" value={inferenceModel} onChange={e => dispatch({ type: 'SET_FIELD', field: 'inferenceModel', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500/40 font-mono" placeholder="e.g. meta-llama/Llama-3-8B" /><div className="absolute right-3 top-3.5 text-muted-foreground pointer-events-none group-focus-within/input:text-blue-500"><Cloud className="w-4 h-4" /></div></div></div>
            </div>
        </div>
    );
}

function VllmSettings({ vllmImage, maxModelLen, gpuUtil, dispatch }: { vllmImage: string; maxModelLen: string; gpuUtil: string; dispatch: React.Dispatch<Action> }) {
    return (
        <m.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6 hover:border-emerald-500/30 transition-all duration-300">
            <div className="flex items-center gap-2 mb-6 text-foreground"><Zap className="w-4 h-4 text-emerald-500 dark:text-emerald-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">vLLM Optimization</h3></div>
            <div className="space-y-6">
                <div className="space-y-2"><label htmlFor="vllm-image" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Container Image</label><input id="vllm-image" value={vllmImage} onChange={e => dispatch({ type: 'SET_FIELD', field: 'vllmImage', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono text-sm" /></div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-2"><label htmlFor="max-model-len" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Max Model Length</label><input id="max-model-len" value={maxModelLen} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
                    <div className="space-y-2"><label htmlFor="gpu-util" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">GPU Util</label><input id="gpu-util" value={gpuUtil} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuUtil', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-emerald-500/40 font-mono" /></div>
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
                <div className="space-y-2"><label htmlFor="git-repo" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Git Repository</label><input id="git-repo" value={gitRepo} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gitRepo', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm" placeholder="https://..." /></div>
                <div className="space-y-2"><label htmlFor="training-script" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Training Command</label><textarea id="training-script" rows={3} value={trainingScript} onChange={e => dispatch({ type: 'SET_FIELD', field: 'trainingScript', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm resize-none" /></div>
                <div className="space-y-2"><label htmlFor="dataset-url" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Dataset URL</label><input id="dataset-url" value={datasetUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'datasetUrl', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-amber-500/40 font-mono text-sm" /></div>
            </div>
        </m.div>
    );
}

function EnvironmentSecrets({ hfToken, dispatch }: { hfToken: string; dispatch: React.Dispatch<Action> }) {
    return (
        <div className="bg-card backdrop-blur-xl border border-border rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-6 text-foreground"><ShieldCheck className="w-4 h-4 text-purple-600 dark:text-purple-400" /><h3 className="text-sm font-bold uppercase tracking-wider font-mono">Environment Secrets</h3></div>
            <div className="space-y-2"><label htmlFor="hf-token" className="text-xs font-bold text-muted-foreground uppercase tracking-tighter ml-1">Hugging Face Token</label><input id="hf-token" type="password" value={hfToken} onChange={e => dispatch({ type: 'SET_FIELD', field: 'hfToken', value: e.target.value })} className="w-full bg-background border border-border rounded-xl px-4 py-3 text-foreground focus:outline-none focus:ring-2 focus:ring-purple-500/40 font-mono text-sm" placeholder="hf_••••••••" /><p className="text-[10px] text-muted-foreground font-mono mt-2">Required for gated models.</p></div>
        </div>
    );
}

function ProTip() {
    return (
        <div className="bg-blue-600/10 border border-blue-500/20 rounded-2xl p-6">
            <h4 className="text-sm font-bold text-blue-600 dark:text-blue-400 mb-2">Pro Tip</h4>
            <p className="text-xs text-blue-600/70 dark:text-blue-300/70 leading-relaxed font-mono">Wait for <span className="text-blue-600 dark:text-blue-400 underline">STOPPED</span> state before applying major changes.</p>
        </div>
    );
}
