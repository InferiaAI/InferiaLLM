import { useState, useEffect } from "react"
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
import { motion, AnimatePresence } from "framer-motion"

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

export default function DeploymentConfig({ deployment, onUpdate }: DeploymentConfigProps) {
    const [loading, setLoading] = useState(false)
    const [config, setConfig] = useState<any>({})
    const [replicas, setReplicas] = useState(deployment?.replicas || 1)
    const [inferenceModel, setInferenceModel] = useState(deployment?.inference_model || "")

    // vLLM specific
    const [vllmImage, setVllmImage] = useState("")
    const [maxModelLen, setMaxModelLen] = useState("")
    const [gpuUtil, setGpuUtil] = useState("")

    // Training specific
    const [gitRepo, setGitRepo] = useState("")
    const [trainingScript, setTrainingScript] = useState("")
    const [datasetUrl, setDatasetUrl] = useState("")

    // Common
    const [hfToken, setHfToken] = useState("")

    const isTraining = deployment?.workload_type === "training"
    const isEmbedding = deployment?.model_type === "embedding" || deployment?.engine === "infinity" || deployment?.engine === "tei"
    const isVllm = deployment?.engine === "vllm"

    useEffect(() => {
        if (deployment?.configuration) {
            const c = deployment.configuration
            setConfig(c)

            if (isVllm) {
                setVllmImage(c.image || "vllm/vllm-openai:latest")
                if (c.cmd) {
                    const mLenIdx = c.cmd.indexOf("--max-model-len")
                    if (mLenIdx !== -1) setMaxModelLen(c.cmd[mLenIdx + 1])

                    const gUtilIdx = c.cmd.indexOf("--gpu-memory-utilization")
                    if (gUtilIdx !== -1) setGpuUtil(c.cmd[gUtilIdx + 1])
                }
                if (c.env?.HF_TOKEN) setHfToken(c.env.HF_TOKEN)
            }

            if (isTraining) {
                setGitRepo(c.git_repo || "")
                setTrainingScript(c.training_script || "")
                setDatasetUrl(c.dataset_url || "")
                setHfToken(c.hf_token || "")
            }
        }
    }, [deployment, isVllm, isTraining])

    const handleSave = async () => {
        setLoading(true)
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
                if (hfToken) {
                    updatedConfig.env = { ...updatedConfig.env, HF_TOKEN: hfToken }
                }
            }

            if (isTraining) {
                updatedConfig.git_repo = gitRepo
                updatedConfig.training_script = trainingScript
                updatedConfig.dataset_url = datasetUrl
                updatedConfig.hf_token = hfToken
            }

            const payload: any = {
                configuration: updatedConfig,
                replicas: replicas,
                inference_model: inferenceModel
            }

            await computeApi.patch(`/deployment/update/${deployment.id || deployment.deployment_id}`, payload)
            toast.success("Configuration updated successfully")
            if (onUpdate) onUpdate()
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to update configuration")
        } finally {
            setLoading(false)
        }
    }

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-6"
        >
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg">
                        <Settings2 className="w-5 h-5 text-blue-500" />
                    </div>
                    <div>
                        <h2 className="text-xl font-bold tracking-tight">Deployment Configuration</h2>
                        <p className="text-sm text-muted-foreground font-mono">Customize engine parameters and runtime settings</p>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <button
                        onClick={() => window.location.reload()}
                        className="p-2 text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800 rounded-lg transition-all"
                        title="Reset changes"
                    >
                        <RotateCcw className="w-5 h-5" />
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={loading}
                        className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20 active:scale-95 disabled:opacity-50"
                    >
                        {loading ? <Zap className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                        Save Changes
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                    {/* General Settings */}
                    <div className="group bg-zinc-900/50 backdrop-blur-xl border border-zinc-800 rounded-2xl p-6 hover:border-blue-500/30 transition-all duration-300">
                        <div className="flex items-center gap-2 mb-6 text-zinc-100">
                            <Server className="w-4 h-4 text-blue-400" />
                            <h3 className="text-sm font-bold uppercase tracking-wider font-mono">General Settings</h3>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div className="space-y-2">
                                <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Replicas</label>
                                <div className="relative group/input">
                                    <input
                                        type="number"
                                        min="1"
                                        value={replicas}
                                        onChange={e => setReplicas(parseInt(e.target.value))}
                                        className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition-all font-mono"
                                    />
                                    <div className="absolute right-3 top-3.5 text-zinc-600 pointer-events-none group-focus-within/input:text-blue-500 transition-colors">
                                        <Layers className="w-4 h-4" />
                                    </div>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Inference Model</label>
                                <div className="relative group/input">
                                    <input
                                        value={inferenceModel}
                                        onChange={e => setInferenceModel(e.target.value)}
                                        className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition-all font-mono"
                                        placeholder="e.g. meta-llama/Llama-2-7b"
                                    />
                                    <div className="absolute right-3 top-3.5 text-zinc-600 pointer-events-none group-focus-within/input:text-blue-500 transition-colors">
                                        <Cloud className="w-4 h-4" />
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Engine Specific Settings */}
                    <AnimatePresence mode="wait">
                        {isVllm && (
                            <motion.div
                                initial={{ opacity: 0, x: -20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: 20 }}
                                className="bg-zinc-900/50 backdrop-blur-xl border border-zinc-800 rounded-2xl p-6 hover:border-emerald-500/30 transition-all duration-300"
                            >
                                <div className="flex items-center gap-2 mb-6 text-zinc-100">
                                    <Zap className="w-4 h-4 text-emerald-400" />
                                    <h3 className="text-sm font-bold uppercase tracking-wider font-mono">vLLM Optimization</h3>
                                </div>

                                <div className="space-y-6">
                                    <div className="space-y-2">
                                        <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Container Image</label>
                                        <input
                                            value={vllmImage}
                                            onChange={e => setVllmImage(e.target.value)}
                                            className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 focus:border-emerald-500 transition-all font-mono text-sm"
                                        />
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        <div className="space-y-2">
                                            <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Max Model Length</label>
                                            <input
                                                value={maxModelLen}
                                                onChange={e => setMaxModelLen(e.target.value)}
                                                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 focus:border-emerald-500 transition-all font-mono"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">GPU Util</label>
                                            <input
                                                value={gpuUtil}
                                                onChange={e => setGpuUtil(e.target.value)}
                                                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 focus:border-emerald-500 transition-all font-mono"
                                            />
                                        </div>
                                    </div>
                                </div>
                            </motion.div>
                        )}

                        {isTraining && (
                            <motion.div
                                initial={{ opacity: 0, x: -20 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: 20 }}
                                className="bg-zinc-900/50 backdrop-blur-xl border border-zinc-800 rounded-2xl p-6 hover:border-amber-500/30 transition-all duration-300"
                            >
                                <div className="flex items-center gap-2 mb-6 text-zinc-100">
                                    <Terminal className="w-4 h-4 text-amber-400" />
                                    <h3 className="text-sm font-bold uppercase tracking-wider font-mono">Training Orchestration</h3>
                                </div>

                                <div className="space-y-6">
                                    <div className="space-y-2">
                                        <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Git Repository</label>
                                        <input
                                            value={gitRepo}
                                            onChange={e => setGitRepo(e.target.value)}
                                            className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-amber-500/40 focus:border-amber-500 transition-all font-mono text-sm"
                                            placeholder="https://github.com/tenant/repo.git"
                                        />
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Training Command</label>
                                        <textarea
                                            rows={3}
                                            value={trainingScript}
                                            onChange={e => setTrainingScript(e.target.value)}
                                            className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-amber-500/40 focus:border-amber-500 transition-all font-mono text-sm resize-none"
                                            placeholder="python3 train.py --config config.yaml"
                                        />
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Dataset URL</label>
                                        <input
                                            value={datasetUrl}
                                            onChange={e => setDatasetUrl(e.target.value)}
                                            className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-amber-500/40 focus:border-amber-500 transition-all font-mono text-sm"
                                        />
                                    </div>
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                </div>

                <div className="space-y-6">
                    {/* Secrets Section */}
                    <div className="bg-zinc-900/50 backdrop-blur-xl border border-zinc-800 rounded-2xl p-6">
                        <div className="flex items-center gap-2 mb-6 text-zinc-100">
                            <ShieldCheck className="w-4 h-4 text-purple-400" />
                            <h3 className="text-sm font-bold uppercase tracking-wider font-mono">Environment Secrets</h3>
                        </div>

                        <div className="space-y-2">
                            <label className="text-xs font-bold text-zinc-500 uppercase tracking-tighter ml-1">Hugging Face Token</label>
                            <input
                                type="password"
                                value={hfToken}
                                onChange={e => setHfToken(e.target.value)}
                                className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-purple-500/40 focus:border-purple-500 transition-all font-mono text-sm"
                                placeholder="hf_••••••••••••••••"
                            />
                            <p className="text-[10px] text-zinc-500 font-mono mt-2 leading-relaxed">Required for accessing gated models on Hugging Face Hub.</p>
                        </div>
                    </div>

                    {/* Help/Info card */}
                    <div className="bg-blue-600/10 border border-blue-500/20 rounded-2xl p-6">
                        <h4 className="text-sm font-bold text-blue-400 mb-2">Pro Tip</h4>
                        <p className="text-xs text-blue-300/70 leading-relaxed font-mono">
                            Wait for the deployment to reach the <span className="text-blue-400 underline">STOPPED</span> state before applying major engine configuration changes to ensure a clean transition.
                        </p>
                    </div>
                </div>
            </div>
        </motion.div>
    )
}
