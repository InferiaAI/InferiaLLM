import { useState, useEffect } from "react"
import { Cpu, Server, Check, Zap, Globe, AlertCircle, ArrowRight } from "lucide-react"
import { toast } from "sonner"
import { useNavigate, Link } from "react-router-dom"
import { cn } from "@/lib/utils"
import { useAuth } from "@/context/AuthContext"
import { computeApi } from "@/lib/api"
import { useQuery } from "@tanstack/react-query"
import { ConfigService } from "@/services/configService"

// Mock Providers
const providerMeta = [
    {
        id: "nosana",
        name: "Nosana Network",
        description: "Decentralized GPU Compute grid. Cheapest and fastest for inference.",
        icon: Globe,
        color: "text-green-500 bg-green-500/10",
        recommended: true,
        category: "depin",
        configPath: "/dashboard/settings/providers/depin/nosana"
    },
    {
        id: "akash",
        name: "Akash Network",
        description: "Decentralized cloud compute. Open-source marketplace for GPUs.",
        icon: Cpu,
        color: "text-purple-500 bg-purple-500/10",
        category: "depin",
        configPath: "/dashboard/settings/providers/depin/akash"
    },
    {
        id: "aws",
        name: "AWS / Cloud",
        description: "Managed EC2 instances. High reliability, higher cost. (Coming Soon)",
        icon: Server,
        color: "text-blue-500 bg-blue-500/10",
        disabled: true,
        category: "cloud",
        configPath: "/dashboard/settings/providers/cloud/aws"
    }
]

export default function NewPool() {
    const navigate = useNavigate()
    const { user, organizations } = useAuth()
    const [step, setStep] = useState(1)
    const [selectedProvider, setSelectedProvider] = useState<string>("")
    const [selectedResource, setSelectedResource] = useState<any>(null)
    const [poolName, setPoolName] = useState("")
    const [isCreating, setIsCreating] = useState(false)
    const [availableResources, setAvailableResources] = useState<any[]>([])
    const [loadingResources, setLoadingResources] = useState(false)

    // Check Configuration
    const { data: config, isLoading: loadingConfig } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    const isProviderConfigured = (pid: string) => {
        if (!config) return false;
        const depin = config.depin || {};
        const cloud = config.cloud || {};

        switch (pid) {
            case "nosana":
                return !!depin.nosana?.wallet_private_key;
            case "akash":
                return !!depin.akash?.mnemonic;
            case "aws":
                return !!cloud.aws?.access_key_id;
            default: return false;
        }
    };

    const providers = providerMeta.map(p => ({
        ...p,
        isConfigured: isProviderConfigured(p.id)
    }));

    useEffect(() => {
        if (selectedProvider && step === 2) {
            fetchResources(selectedProvider)
        }
    }, [selectedProvider, step])

    const fetchResources = async (provider: string) => {
        setLoadingResources(true)
        try {
            const res = await computeApi.get(`/deployment/provider/resources?provider=${provider}`)
            setAvailableResources(res.data.resources || [])
        } catch (error) {
            toast.error("Failed to load compute resources")
            console.error(error)
        } finally {
            setLoadingResources(false)
        }
    }

    const handleProviderSelect = (id: string) => {
        setSelectedProvider(id)
        setStep(2)
    }

    const handleResourceSelect = (resource: any) => {
        setSelectedResource(resource)
    }

    const handleCreate = async () => {
        if (!poolName) {
            toast.error("Please give your pool a name")
            return
        }

        const targetOrgId = user?.org_id || organizations?.[0]?.id;
        if (!targetOrgId) {
            toast.error("Organization context missing. Please reload.")
            return
        }

        setIsCreating(true)

        try {
            const payload = {
                pool_name: poolName,
                owner_type: "user",
                owner_id: targetOrgId,
                provider: selectedProvider,
                allowed_gpu_types: [selectedResource.gpu_type],
                max_cost_per_hour: selectedResource.price_per_hour,
                is_dedicated: false,
                provider_pool_id: selectedResource.metadata?.market_address || selectedResource.provider_resource_id,
                scheduling_policy_json: JSON.stringify({ strategy: "best_fit" })
            }

            await computeApi.post("/deployment/createpool", payload)

            toast.success("Compute Pool created successfully!")
            navigate("/dashboard/compute/pools")
        } catch (error: any) {
            toast.error(error.response?.data?.detail || error.message)
        } finally {
            setIsCreating(false)
        }
    }

    if (loadingConfig) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[400px]">
                <Cpu className="w-12 h-12 text-primary/20 animate-pulse mb-4" />
                <p className="text-muted-foreground animate-pulse">Checking providers...</p>
            </div>
        )
    }

    return (
        <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-slate-900 dark:text-zinc-50">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Create New Compute Pool</h2>
                <p className="text-muted-foreground mt-2">
                    Create a pool of compute resources to deploy your models on.
                </p>
            </div>

            {/* Progress Steps */}
            <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
                <div className={cn("flex items-center gap-2", step >= 1 && "text-blue-600 dark:text-blue-400")}>
                    <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-all", step >= 1 ? "bg-blue-600 text-white border-blue-600 dark:border-blue-500 dark:bg-blue-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800")}>1</div>
                    Select Provider
                </div>
                <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
                <div className={cn("flex items-center gap-2", step >= 2 && "text-blue-600 dark:text-blue-400")}>
                    <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-all", step >= 2 ? "bg-blue-600 text-white border-blue-600 dark:border-blue-500 dark:bg-blue-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800")}>2</div>
                    Compute Config
                </div>
                <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
                <div className={cn("flex items-center gap-2", step >= 3 && "text-blue-600 dark:text-blue-400")}>
                    <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-all", step >= 3 ? "bg-blue-600 text-white border-blue-600 dark:border-blue-500 dark:bg-blue-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800")}>3</div>
                    Review & Create
                </div>
            </div>

            {/* Step 1: Provider Selection */}
            {step === 1 && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {providers.map((p) => {
                        if (p.isConfigured || p.disabled) {
                            return (
                                <button
                                    key={p.id}
                                    disabled={p.disabled}
                                    onClick={() => handleProviderSelect(p.id)}
                                    className={cn(
                                        "text-left group relative p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:border-blue-500/50 dark:hover:border-blue-500/50 transition-all hover:shadow-md flex flex-col gap-4",
                                        p.disabled && "opacity-50 cursor-not-allowed hover:border-slate-200 dark:hover:border-zinc-800 hover:shadow-none bg-slate-50 dark:bg-zinc-900/50"
                                    )}
                                >
                                    <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center transition-colors", p.color)}>
                                        <p.icon className="w-6 h-6" />
                                    </div>
                                    <div>
                                        <h3 className="font-bold text-lg mb-1 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors uppercase tracing-tight text-xs">{p.name}</h3>
                                        <p className="text-sm text-slate-500 dark:text-zinc-400 leading-relaxed">{p.description}</p>
                                    </div>
                                    {p.recommended && (
                                        <span className="absolute top-4 right-4 text-[10px] font-bold uppercase tracking-wider bg-green-100 text-green-700 px-2 py-1 rounded">Recommended</span>
                                    )}
                                </button>
                            );
                        } else {
                            // Unconfigured Card
                            return (
                                <Link
                                    key={p.id}
                                    to={p.configPath}
                                    className="text-left group relative p-6 rounded-xl border border-dashed border-slate-300 dark:border-zinc-800 bg-slate-50/30 dark:bg-zinc-900/20 hover:border-slate-400 dark:hover:border-zinc-700 transition-all flex flex-col gap-4"
                                >
                                    <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center opacity-40grayscale", p.color)}>
                                        <p.icon className="w-6 h-6" />
                                    </div>
                                    <div>
                                        <h3 className="font-bold text-lg mb-1 text-slate-400 dark:text-zinc-500">{p.name}</h3>
                                        <p className="text-xs text-slate-400 dark:text-zinc-600">Configuration required to create pools on this network.</p>
                                    </div>
                                    <div className="mt-auto flex items-center gap-1.5 text-blue-600 dark:text-blue-400 text-xs font-bold uppercase tracking-wider opacity-0 group-hover:opacity-100 transition-opacity">
                                        Connect Provider <ArrowRight className="w-3 h-3" />
                                    </div>
                                </Link>
                            );
                        }
                    })}
                </div>
            )}

            {/* Step 2: Configure Compute */}
            {step === 2 && (
                <div className="space-y-6">
                    {loadingResources ? (
                        <div className="text-center py-12 text-slate-500">
                            Loading available resources...
                        </div>
                    ) : availableResources.length === 0 ? (
                        <div className="text-center py-12 text-slate-500">
                            No resources found for this provider.
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {availableResources.map((res: any) => (
                                <div
                                    key={res.provider_resource_id}
                                    onClick={() => handleResourceSelect(res)}
                                    className={cn(
                                        "cursor-pointer p-4 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 transition-all relative",
                                        selectedResource?.provider_resource_id === res.provider_resource_id
                                            ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-sm"
                                            : "hover:border-blue-400/30 dark:hover:border-blue-600/30"
                                    )}
                                >
                                    <div className="flex justify-between items-start mb-2">
                                        <div className="p-2 bg-slate-100 dark:bg-zinc-800 rounded-md">
                                            <Cpu className="w-5 h-5 text-slate-700 dark:text-zinc-200" />
                                        </div>
                                        <span className="font-bold text-green-600 dark:text-green-400">${res.price_per_hour}/hr</span>
                                    </div>
                                    <h4 className="font-bold">{res.provider_resource_id}</h4>
                                    <p className="text-sm text-slate-500 dark:text-zinc-400">{res.gpu_type} ({res.gpu_memory_gb}GB VRAM)</p>
                                    <div className="mt-2 flex gap-2 text-xs text-slate-400 dark:text-zinc-500">
                                        <span>{res.vcpu} vCPU</span> • <span>{res.ram_gb}GB RAM</span>
                                    </div>

                                    {selectedResource?.provider_resource_id === res.provider_resource_id && (
                                        <div className="absolute top-4 right-4 w-5 h-5 bg-blue-600 text-white rounded-full flex items-center justify-center">
                                            <Check className="w-3 h-3" />
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="flex justify-between pt-6">
                        <button
                            onClick={() => setStep(1)}
                            className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200"
                        >
                            Back
                        </button>
                        <button
                            onClick={() => selectedResource && setStep(3)}
                            disabled={!selectedResource}
                            className="px-6 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Continue
                        </button>
                    </div>
                </div>
            )}

            {/* Step 3: Review */}
            {step === 3 && (
                <div className="max-w-xl mx-auto space-y-6">
                    <div className="p-6 rounded-xl border bg-slate-50 dark:bg-zinc-900/50 dark:border-zinc-800 space-y-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Pool Name</label>
                            <input
                                autoFocus
                                className="w-full px-3 py-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-700 focus:ring-2 focus:ring-blue-500/20 outline-none dark:text-zinc-100"
                                placeholder="e.g. My Nosana Pool"
                                value={poolName}
                                onChange={(e) => setPoolName(e.target.value)}
                            />
                        </div>

                        <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60 space-y-3">
                            <div className="flex justify-between text-sm">
                                <span className="text-slate-500 dark:text-zinc-400">Provider</span>
                                <span className="font-medium capitalize">{providers.find(p => p.id === selectedProvider)?.name}</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-slate-500 dark:text-zinc-400">GPU Type</span>
                                <span className="font-medium">{selectedResource?.gpu_type}</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-slate-500 dark:text-zinc-400">Cost per Hour</span>
                                <span className="font-medium">${selectedResource?.price_per_hour}</span>
                            </div>
                        </div>
                    </div>

                    <div className="flex gap-3">
                        <button
                            onClick={() => setStep(2)}
                            className="flex-1 px-4 py-2 text-sm font-medium border rounded-md hover:bg-slate-50 dark:hover:bg-zinc-800 text-slate-700 dark:text-zinc-300 bg-white dark:bg-zinc-900 dark:border-zinc-700"
                        >
                            Back
                        </button>
                        <button
                            onClick={handleCreate}
                            disabled={isCreating}
                            className="flex-[2] px-6 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
                        >
                            {isCreating ? (
                                <>Creating Pool...</>
                            ) : (
                                <><Zap className="w-4 h-4" /> Create Pool</>
                            )}
                        </button>
                    </div>
                </div>
            )}
        </div>
    )
}
