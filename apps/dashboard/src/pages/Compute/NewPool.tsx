import { useReducer, useEffect, useMemo } from "react"
import { Cpu, Server, Check, Zap, Globe, ArrowRight, Search, Key } from "lucide-react"
import { toast } from "sonner"
import { useNavigate, Link } from "react-router-dom"
import { cn } from "@/lib/utils"
import { useAuth } from "@/context/AuthContext"
import { computeApi } from "@/lib/api"
import { useQuery } from "@tanstack/react-query"
import { ConfigService, type NosanaApiKeyResponse } from "@/services/configService"

// Provider icons mapping
const providerIcons: Record<string, React.ComponentType<{ className?: string }>> = {
    nosana: Globe,
    akash: Cpu,
    aws: Server,
    k8s: Server,
    skypilot: Server,
}

// Provider color mapping
const providerColors: Record<string, string> = {
    nosana: "text-green-500 bg-green-500/10",
    akash: "text-purple-500 bg-purple-500/10",
    aws: "text-blue-500 bg-blue-500/10",
    k8s: "text-orange-500 bg-orange-500/10",
    skypilot: "text-cyan-500 bg-cyan-500/10",
}

// Provider descriptions
const providerDescriptions: Record<string, string> = {
    nosana: "Decentralized GPU Compute grid. Cheapest and fastest for inference.",
    akash: "Decentralized cloud compute. Open-source marketplace for GPUs.",
    aws: "Managed EC2 instances. High reliability, higher cost.",
    k8s: "On-premises Kubernetes cluster. Full control and privacy.",
    skypilot: "Multi-cloud orchestration. Unified interface for AWS/GCP/Azure.",
}

interface NewPoolState {
    step: number;
    selectedProvider: string;
    selectedResource: any;
    poolName: string;
    isCreating: boolean;
    availableResources: any[];
    loadingResources: boolean;
    searchQuery: string;
    minVram: number;
    sortBy: "price_asc" | "price_desc" | "memory";
    providerCredentials: NosanaApiKeyResponse[];
    selectedCredential: string;
    loadingCredentials: boolean;
}

type NewPoolAction =
    | { type: "SET_STEP"; payload: number }
    | { type: "SET_PROVIDER"; payload: string }
    | { type: "SET_RESOURCE"; payload: any }
    | { type: "SET_POOL_NAME"; payload: string }
    | { type: "SET_CREATING"; payload: boolean }
    | { type: "SET_RESOURCES"; payload: any[] }
    | { type: "SET_LOADING_RESOURCES"; payload: boolean }
    | { type: "SET_SEARCH"; payload: string }
    | { type: "SET_VRAM"; payload: number }
    | { type: "SET_SORT"; payload: "price_asc" | "price_desc" | "memory" }
    | { type: "SET_CREDENTIALS"; payload: NosanaApiKeyResponse[] }
    | { type: "SET_SELECTED_CREDENTIAL"; payload: string }
    | { type: "SET_LOADING_CREDENTIALS"; payload: boolean };

const initialState: NewPoolState = {
    step: 1,
    selectedProvider: "",
    selectedResource: null,
    poolName: "",
    isCreating: false,
    availableResources: [],
    loadingResources: false,
    searchQuery: "",
    minVram: 0,
    sortBy: "price_asc",
    providerCredentials: [],
    selectedCredential: "",
    loadingCredentials: false,
};

function poolReducer(state: NewPoolState, action: NewPoolAction): NewPoolState {
    switch (action.type) {
        case "SET_STEP": return { ...state, step: action.payload };
        case "SET_PROVIDER": return { ...state, selectedProvider: action.payload };
        case "SET_RESOURCE": return { ...state, selectedResource: action.payload };
        case "SET_POOL_NAME": return { ...state, poolName: action.payload };
        case "SET_CREATING": return { ...state, isCreating: action.payload };
        case "SET_RESOURCES": return { ...state, availableResources: action.payload };
        case "SET_LOADING_RESOURCES": return { ...state, loadingResources: action.payload };
        case "SET_SEARCH": return { ...state, searchQuery: action.payload };
        case "SET_VRAM": return { ...state, minVram: action.payload };
        case "SET_SORT": return { ...state, sortBy: action.payload };
        case "SET_CREDENTIALS": return { ...state, providerCredentials: action.payload };
        case "SET_SELECTED_CREDENTIAL": return { ...state, selectedCredential: action.payload };
        case "SET_LOADING_CREDENTIALS": return { ...state, loadingCredentials: action.payload };
        default: return state;
    }
}

export default function NewPool() {
    const navigate = useNavigate()
    const { user, organizations } = useAuth()
    const [state, dispatch] = useReducer(poolReducer, initialState);
    const {
        step,
        selectedProvider,
        selectedResource,
        poolName,
        isCreating,
        availableResources,
        loadingResources,
        searchQuery,
        minVram,
        sortBy,
        providerCredentials,
        selectedCredential,
        loadingCredentials
    } = state;

    // Fetch provider configuration
    const { data: config, isLoading: loadingConfig } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    // NEW: Fetch registered providers dynamically from API
    const { data: providersData, isLoading: loadingProviders } = useQuery({
        queryKey: ["registeredProviders"],
        queryFn: async () => {
            try {
                const res = await computeApi.get('/inventory/providers')
                return res.data.providers
            } catch (error) {
                console.error("Failed to fetch providers:", error)
                return null
            }
        },
        staleTime: 5 * 60 * 1000,
    })

    const providerMeta = useMemo(() => {
        if (!providersData) {
            return [
                {
                    id: "nosana",
                    name: "Nosana Network",
                    description: providerDescriptions.nosana,
                    icon: providerIcons.nosana,
                    color: providerColors.nosana,
                    recommended: true,
                    category: "depin",
                    configPath: "/dashboard/settings/providers/depin/nosana"
                },
                {
                    id: "akash",
                    name: "Akash Network",
                    description: providerDescriptions.akash,
                    icon: providerIcons.akash,
                    color: providerColors.akash,
                    category: "depin",
                    configPath: "/dashboard/settings/providers/depin/akash"
                },
                {
                    id: "aws",
                    name: "AWS / Cloud",
                    description: providerDescriptions.aws,
                    icon: providerIcons.aws,
                    color: providerColors.aws,
                    disabled: true,
                    category: "cloud",
                    configPath: "/dashboard/settings/providers/cloud/aws"
                }
            ]
        }

        return Object.entries(providersData).map(([id, data]: [string, any]) => ({
            id,
            name: `${id.charAt(0).toUpperCase() + id.slice(1)} Network`,
            description: providerDescriptions[id] || `${id} compute provider`,
            icon: providerIcons[id] || Server,
            color: providerColors[id] || "text-slate-500 bg-slate-500/10",
            category: data.adapter_type || "cloud",
            configPath: `/dashboard/settings/providers/${data.adapter_type || 'cloud'}/${id}`,
            capabilities: data.capabilities,
            recommended: data.adapter_type === 'depin' && id === 'nosana',
        }))
    }, [providersData]);

    const isProviderConfigured = (pid: string) => {
        if (!config) return false;
        const depin = config.depin || {};
        const cloud = config.cloud || {};

        switch (pid) {
            case "nosana":
                return !!(depin.nosana?.wallet_private_key || depin.nosana?.api_key || (depin.nosana?.api_keys && depin.nosana.api_keys.length > 0));
            case "akash":
                return !!depin.akash?.mnemonic;
            case "aws":
                return !!cloud.aws?.access_key_id;
            case "k8s":
                return true;
            default:
                return !!(depin[pid] || cloud[pid]);
        }
    };

    const providers = useMemo(() => providerMeta.map(p => ({
        ...p,
        isConfigured: isProviderConfigured(p.id)
    })), [providerMeta, config]);

    useEffect(() => {
        if (selectedProvider && step === 2) {
            const fetchResources = async () => {
                dispatch({ type: "SET_LOADING_RESOURCES", payload: true })
                try {
                    const res = await computeApi.get(`/deployment/provider/resources?provider=${selectedProvider}`)
                    dispatch({ type: "SET_RESOURCES", payload: res.data.resources || [] })
                } catch (error) {
                    toast.error("Failed to load compute resources")
                    console.error(error)
                } finally {
                    dispatch({ type: "SET_LOADING_RESOURCES", payload: false })
                }
            }

            const loadProviderCredentials = async () => {
                try {
                    dispatch({ type: "SET_LOADING_CREDENTIALS", payload: true })
                    const credentials = await ConfigService.listProviderCredentials(selectedProvider)
                    const mapped = credentials.map(c => ({
                        name: c.name,
                        is_active: c.is_active,
                        created_at: c.created_at
                    }))
                    dispatch({ type: "SET_CREDENTIALS", payload: mapped })
                    const active = mapped.find(k => k.is_active)
                    if (active) {
                        dispatch({ type: "SET_SELECTED_CREDENTIAL", payload: active.name })
                    }
                } catch (error) {
                    console.error(`Failed to load ${selectedProvider} credentials:`, error)
                } finally {
                    dispatch({ type: "SET_LOADING_CREDENTIALS", payload: false })
                }
            }

            void fetchResources()
            if (["nosana", "akash"].includes(selectedProvider)) {
                void loadProviderCredentials()
            }
        }
    }, [selectedProvider, step])

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

        dispatch({ type: "SET_CREATING", payload: true })

        try {
            const payload: any = {
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

            if (selectedCredential) {
                payload.provider_credential_name = selectedCredential
            }

            await computeApi.post("/deployment/createpool", payload)
            toast.success("Compute Pool created successfully!")
            navigate("/dashboard/compute/pools")
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message
            toast.error(errorDetail)
            console.error(error)
        } finally {
            dispatch({ type: "SET_CREATING", payload: false })
        }
    }

    if (loadingConfig || loadingProviders) {
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
            <StepProgress currentStep={step} />

            {/* Step 1: Provider Selection */}
            {step === 1 && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {providers.map((p) => (
                        <ProviderCard
                            key={p.id}
                            provider={p}
                            onSelect={(id) => {
                                dispatch({ type: "SET_PROVIDER", payload: id });
                                dispatch({ type: "SET_STEP", payload: 2 });
                            }}
                        />
                    ))}
                </div>
            )}

            {/* Step 2: Configure Compute */}
            {step === 2 && (
                <div className="space-y-6">
                    <ResourceFilter
                        searchQuery={searchQuery}
                        setSearchQuery={(q) => dispatch({ type: "SET_SEARCH", payload: q })}
                        minVram={minVram}
                        setMinVram={(v) => dispatch({ type: "SET_VRAM", payload: v })}
                        sortBy={sortBy}
                        setSortBy={(s) => dispatch({ type: "SET_SORT", payload: s })}
                    />

                    {loadingResources ? (
                        <div className="text-center py-12 text-slate-500">Loading available resources...</div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {availableResources
                                .filter(res => {
                                    const matchesSearch = res.gpu_type.toLowerCase().includes(searchQuery.toLowerCase()) ||
                                        res.provider_resource_id.toLowerCase().includes(searchQuery.toLowerCase());
                                    const matchesVram = res.gpu_memory_gb >= minVram;
                                    return matchesSearch && matchesVram;
                                })
                                .sort((a, b) => {
                                    if (sortBy === "price_asc") return a.price_per_hour - b.price_per_hour;
                                    if (sortBy === "price_desc") return b.price_per_hour - a.price_per_hour;
                                    if (sortBy === "memory") return b.gpu_memory_gb - a.gpu_memory_gb;
                                    return 0;
                                })
                                .map((res: any) => (
                                    <ResourceCard
                                        key={res.provider_resource_id}
                                        resource={res}
                                        isSelected={selectedResource?.provider_resource_id === res.provider_resource_id}
                                        onSelect={(r) => dispatch({ type: "SET_RESOURCE", payload: r })}
                                    />
                                ))}
                        </div>
                    )}

                    <div className="flex justify-between pt-6">
                        <button
                            onClick={() => dispatch({ type: "SET_STEP", payload: 1 })}
                            className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200"
                        >
                            Back
                        </button>
                        <button
                            onClick={() => selectedResource && dispatch({ type: "SET_STEP", payload: 3 })}
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
                            <label htmlFor="pool-name" className="text-sm font-medium">Pool Name</label>
                            <input
                                id="pool-name"
                                className="w-full px-3 py-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-700 focus:ring-2 focus:ring-blue-500/20 outline-none dark:text-zinc-100"
                                placeholder="e.g. My Nosana Pool"
                                value={poolName}
                                onChange={(e) => dispatch({ type: "SET_POOL_NAME", payload: e.target.value })}
                            />
                        </div>

                        <PoolDetails
                            providerName={providers.find(p => p.id === selectedProvider)?.name}
                            resource={selectedResource}
                        />

                        {providerCredentials.length > 0 && (
                            <CredentialSelection
                                provider={selectedProvider}
                                credentials={providerCredentials}
                                selectedCredential={selectedCredential}
                                setSelectedCredential={(c) => dispatch({ type: "SET_SELECTED_CREDENTIAL", payload: c })}
                                loading={loadingCredentials}
                            />
                        )}
                    </div>

                    <div className="flex gap-3">
                        <button
                            onClick={() => dispatch({ type: "SET_STEP", payload: 2 })}
                            className="flex-1 px-4 py-2 text-sm font-medium border rounded-md hover:bg-slate-50 dark:hover:bg-zinc-800 text-slate-700 dark:text-zinc-300 bg-white dark:bg-zinc-900 dark:border-zinc-700"
                        >
                            Back
                        </button>
                        <button
                            onClick={handleCreate}
                            disabled={isCreating}
                            className="flex-[2] px-6 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
                        >
                            {isCreating ? <>Creating Pool...</> : <><Zap className="w-4 h-4" /> Create Pool</>}
                        </button>
                    </div>
                </div>
            )}
        </div>
    )
}

function StepProgress({ currentStep }: { currentStep: number }) {
    return (
        <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
            {[1, 2, 3].map((s, i) => (
                <div key={s} className="flex items-center gap-4">
                    <div className={cn("flex items-center gap-2", currentStep >= s && "text-blue-600 dark:text-blue-400")}>
                        <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-all", currentStep >= s ? "bg-blue-600 text-white border-blue-600 dark:border-blue-500 dark:bg-blue-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800")}>{s}</div>
                        {s === 1 ? "Select Provider" : s === 2 ? "Compute Config" : "Review & Create"}
                    </div>
                    {i < 2 && <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />}
                </div>
            ))}
        </div>
    )
}

function ProviderCard({ provider: p, onSelect }: { provider: any, onSelect: (id: string) => void }) {
    if (p.isConfigured || p.disabled) {
        return (
            <button
                disabled={p.disabled}
                onClick={() => onSelect(p.id)}
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
                    {p.capabilities && (
                        <div className="mt-2 flex flex-wrap gap-1">
                            {p.capabilities.is_ephemeral && <span className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">Ephemeral</span>}
                            {p.capabilities.pricing_model !== 'fixed' && <span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded capitalize">{p.capabilities.pricing_model}</span>}
                        </div>
                    )}
                </div>
                {p.recommended && <span className="absolute top-4 right-4 text-[10px] font-bold uppercase tracking-wider bg-green-100 text-green-700 px-2 py-1 rounded">Recommended</span>}
            </button>
        )
    }

    return (
        <Link
            to={p.configPath}
            className="text-left group relative p-6 rounded-xl border border-dashed border-slate-300 dark:border-zinc-800 bg-slate-50/30 dark:bg-zinc-900/20 hover:border-slate-400 dark:hover:border-zinc-700 transition-all flex flex-col gap-4"
        >
            <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center opacity-40 grayscale", p.color)}>
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
    )
}

function ResourceFilter({ searchQuery, setSearchQuery, minVram, setMinVram, sortBy, setSortBy }: any) {
    return (
        <div className="flex flex-col md:flex-row gap-3">
            <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                    placeholder="Search GPUs (v100, t4, a100...)"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg outline-none focus:ring-2 focus:ring-blue-500/20 transition-all text-sm"
                />
            </div>
            <select value={minVram} onChange={(e) => setMinVram(Number(e.target.value))} className="px-3 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/20">
                <option value={0}>All Memory</option>
                <option value={8}>8GB+ VRAM</option>
                <option value={16}>16GB+ VRAM</option>
                <option value={24}>24GB+ VRAM</option>
                <option value={40}>40GB+ VRAM</option>
                <option value={80}>80GB+ VRAM</option>
            </select>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as any)} className="px-3 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/20">
                <option value="price_asc">Price: Low to High</option>
                <option value="price_desc">Price: High to Low</option>
                <option value="memory">Memory: High to Low</option>
            </select>
        </div>
    )
}

function ResourceCard({ resource: res, isSelected, onSelect }: { resource: any, isSelected: boolean, onSelect: (r: any) => void }) {
    return (
        <div
            role="button"
            tabIndex={0}
            onClick={() => onSelect(res)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(res) }}
            className={cn(
                "cursor-pointer p-4 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 transition-all relative",
                isSelected ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-sm" : "hover:border-blue-400/30 dark:hover:border-blue-600/30"
            )}
        >
            <div className="flex justify-between items-start mb-2">
                <div className="p-2 bg-slate-100 dark:bg-zinc-800 rounded-md"><Cpu className="w-5 h-5 text-slate-700 dark:text-zinc-200" /></div>
                <span className="font-bold text-green-600 dark:text-green-400">${res.price_per_hour}/hr</span>
            </div>
            <h4 className="font-bold">{res.provider_resource_id}</h4>
            <p className="text-sm text-slate-500 dark:text-zinc-400">{res.gpu_type} ({res.gpu_memory_gb}GB VRAM)</p>
            <div className="mt-2 flex gap-2 text-xs text-slate-400 dark:text-zinc-500">
                <span>{res.vcpu} vCPU</span> • <span>{res.ram_gb}GB RAM</span>
            </div>
            {res.pricing_model && res.pricing_model !== 'fixed' && <div className="mt-1"><span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded capitalize">{res.pricing_model}</span></div>}
            {isSelected && <div className="absolute top-4 right-4 w-5 h-5 bg-blue-600 text-white rounded-full flex items-center justify-center"><Check className="w-3 h-3" /></div>}
        </div>
    )
}

function PoolDetails({ providerName, resource }: { providerName?: string, resource: any }) {
    return (
        <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60 space-y-3">
            <div className="flex justify-between text-sm">
                <span className="text-slate-500 dark:text-zinc-400">Provider</span>
                <span className="font-medium capitalize">{providerName}</span>
            </div>
            <div className="flex justify-between text-sm">
                <span className="text-slate-500 dark:text-zinc-400">GPU Type</span>
                <span className="font-medium">{resource?.gpu_type}</span>
            </div>
            <div className="flex justify-between text-sm">
                <span className="text-slate-500 dark:text-zinc-400">Cost per Hour</span>
                <span className="font-medium">${resource?.price_per_hour}</span>
            </div>
            {resource?.pricing_model && resource?.pricing_model !== 'fixed' && (
                <div className="flex justify-between text-sm">
                    <span className="text-slate-500 dark:text-zinc-400">Pricing Model</span>
                    <span className="font-medium capitalize">{resource?.pricing_model}</span>
                </div>
            )}
        </div>
    )
}

function CredentialSelection({ provider, credentials, selectedCredential, setSelectedCredential, loading }: any) {
    return (
        <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60 space-y-3">
            <div className="space-y-2">
                <label htmlFor="credential-select" className="text-sm font-medium flex items-center gap-2"><Key className="w-4 h-4" /> {provider === "nosana" ? "Nosana API Key" : provider === "akash" ? "Akash Wallet" : "Provider Credential"}</label>
                {loading ? <div className="text-sm text-muted-foreground">Loading credentials...</div> : (
                    <select id="credential-select" value={selectedCredential} onChange={(e) => setSelectedCredential(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-700 focus:ring-2 focus:ring-blue-500/20 outline-none dark:text-zinc-100 text-sm">
                        <option value="">Select a credential...</option>
                        {credentials.filter((key: any) => key.is_active).map((key: any) => <option key={key.name} value={key.name}>{key.name}</option>)}
                    </select>
                )}
                <p className="text-xs text-muted-foreground">Choose which credential to use for this pool</p>
            </div>
        </div>
    )
}
