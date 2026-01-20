import { CheckCircle2, Loader2, AlertCircle, StopCircle, Eye, EyeOff, Copy } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useState } from "react"

interface DeploymentOverviewProps {
    deployment: any
}

export default function DeploymentOverview({ deployment }: DeploymentOverviewProps) {
    const [showRawEndpoint, setShowRawEndpoint] = useState(false)
    if (!deployment) return null

    return (
        <div className="space-y-6">
            {/* Status Overview Card */}
            <div className="bg-card rounded-lg border shadow-sm p-6">
                <h3 className="font-mono text-sm font-bold uppercase tracking-wider mb-6">Status Overview</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Status</div>
                        <div className={cn(
                            "flex items-center gap-2 font-medium",
                            (deployment.state === "READY" || deployment.state === "RUNNING") ? "text-green-600 dark:text-green-500" :
                                deployment.state === "STOPPED" ? "text-slate-500" :
                                    deployment.state === "FAILED" ? "text-red-600" :
                                        "text-yellow-600"
                        )}>
                            {(deployment.state === "READY" || deployment.state === "RUNNING") ? <CheckCircle2 className="w-5 h-5" /> :
                                deployment.state === "STOPPED" || deployment.state === "TERMINATED" ? <StopCircle className="w-5 h-5" /> :
                                    deployment.state === "FAILED" ? <AlertCircle className="w-5 h-5" /> :
                                        <Loader2 className="w-5 h-5 animate-spin" />}
                            {deployment.state || "Unknown"}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Ready Replicas</div>
                        <div className="text-lg font-medium">
                            {(deployment.state === "READY" || deployment.state === "RUNNING") ? "1 / 1" : "0 / 1"}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Updated Replicas</div>
                        <div className="text-lg font-medium">1</div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Available Replicas</div>
                        <div className="text-lg font-medium">
                            {(deployment.state === "READY" || deployment.state === "RUNNING") ? "1" : "0"}
                        </div>
                    </div>
                </div>
            </div>

            {/* Deployment Information Card */}
            <div className="bg-card rounded-lg border shadow-sm p-6">
                <h3 className="font-mono text-sm font-bold uppercase tracking-wider mb-6">Deployment Information</h3>
                <div className="grid md:grid-cols-2 gap-8">
                    <div className="space-y-6">
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Created</div>
                            <div className="font-mono text-sm">{new Date(deployment.created_at).toLocaleString()}</div>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Replicas</div>
                            <div className="font-mono text-sm">1</div>
                        </div>
                    </div>
                    <div className="space-y-6">
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Strategy</div>
                            <div className="font-mono text-sm">RollingUpdate</div>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Selector</div>
                            <div className="flex gap-2">
                                <span className="bg-muted px-2 py-0.5 rounded text-xs font-mono border">app: {deployment.model_name}</span>
                                <span className="bg-muted px-2 py-0.5 rounded text-xs font-mono border">provider: {deployment.provider}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="mt-8 pt-8 border-t">
                    {deployment.workload_type === 'training' ? (
                        <>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Training Monitor</div>
                                    <p className="text-sm text-muted-foreground">Access TensorBoard or training logs directly.</p>
                                </div>
                                {deployment.endpoint_url && (
                                    <span className="px-2 py-1 rounded bg-green-100 text-green-700 text-xs font-mono border border-green-200">Active</span>
                                )}
                            </div>
                            <div className="bg-slate-50 dark:bg-slate-900 p-4 rounded-lg border font-mono text-sm flex justify-between items-center">
                                <div className="flex flex-col gap-1">
                                    <span className="text-muted-foreground text-xs uppercase">TensorBoard URL</span>
                                    {deployment.endpoint_url ? (
                                        <a
                                            href={deployment.endpoint_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="text-blue-600 hover:underline break-all"
                                        >
                                            {deployment.endpoint_url}
                                        </a>
                                    ) : (
                                        <div className="flex items-center gap-2 text-muted-foreground">
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                            <span className="text-xs italic">Waiting for TensorBoard...</span>
                                        </div>
                                    )}
                                </div>
                                {deployment.endpoint_url && (
                                    <a
                                        href={deployment.endpoint_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="ml-4 px-4 py-2 bg-blue-600 text-white rounded-md text-xs font-medium hover:bg-blue-700 transition-colors shrink-0"
                                    >
                                        Open Board
                                    </a>
                                )}
                            </div>

                            <div className="mt-6 grid md:grid-cols-2 gap-6">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Git Repository</div>
                                    <div className="p-3 bg-slate-50 dark:bg-slate-900 rounded border text-sm font-mono break-all">
                                        {deployment.git_repo || "N/A"}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Training Command</div>
                                    <div className="p-3 bg-slate-50 dark:bg-slate-900 rounded border text-sm font-mono break-all overflow-x-auto whitespace-pre-wrap">
                                        {deployment.training_script || "N/A"}
                                    </div>
                                </div>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Raw Inference Endpoint</div>
                                    <p className="text-sm text-muted-foreground">The direct URL to the inference service (hidden by default).</p>
                                </div>
                            </div>
                            <div className="bg-slate-50 dark:bg-slate-900 p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden mb-6">
                                <div className="flex flex-col gap-1 w-full">
                                    {deployment.endpoint_url ? (
                                        <div className="flex items-center gap-2">
                                            <span className="text-primary break-all">
                                                {showRawEndpoint ? deployment.endpoint_url : "•".repeat(deployment.endpoint_url.length)}
                                            </span>
                                        </div>
                                    ) : (
                                        <div className="flex items-center gap-2 text-muted-foreground">
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                            <span className="text-xs italic">Fetching endpoint...</span>
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => setShowRawEndpoint(!showRawEndpoint)}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title={showRawEndpoint ? "Hide Endpoint" : "Show Endpoint"}
                                        disabled={!deployment.endpoint_url}
                                    >
                                        {showRawEndpoint ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                    <button
                                        onClick={() => {
                                            if (deployment.endpoint_url) {
                                                navigator.clipboard.writeText(deployment.endpoint_url);
                                                toast.success("Raw endpoint copied to clipboard");
                                            }
                                        }}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title="Copy Endpoint"
                                        disabled={!deployment.endpoint_url}
                                    >
                                        <Copy className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Inference Endpoint</div>
                                    <p className="text-sm text-muted-foreground">The public endpoint for chat completions.</p>
                                </div>
                                {deployment.endpoint_url && (
                                    <span className="px-2 py-1 rounded bg-green-100 text-green-700 text-xs font-mono border border-green-200">Active</span>
                                )}
                            </div>
                            <div className="bg-slate-50 dark:bg-slate-900 p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden">
                                <div className="flex flex-col gap-1 w-full">
                                    <span className="text-primary break-all">
                                        http://localhost:8001/v1/chat/completion
                                    </span>
                                </div>
                                <button
                                    onClick={() => {
                                        navigator.clipboard.writeText("http://localhost:8001/v1/chat/completion");
                                        toast.success("Endpoint copied to clipboard");
                                    }}
                                    className="ml-4 p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                    title="Copy Endpoint"
                                >
                                    <Copy className="w-4 h-4" />
                                </button>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div >
    )
}
