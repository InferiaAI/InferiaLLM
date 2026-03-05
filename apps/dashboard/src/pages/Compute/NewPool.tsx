import { useReducer, useEffect, useMemo } from "react"
import { Cpu, Server, Check, Zap, Globe, ArrowRight, Search, Key, Cloud } from "lucide-react"
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
    gcp: Cloud,
    k8s: Server,
    skypilot: Server,
}

// Provider color mapping
const providerColors: Record<string, string> = {
    nosana: "text-green-500 bg-green-500/10",
    akash: "text-purple-500 bg-purple-500/10",
    aws: "text-emerald-500 bg-emerald-500/10",
    gcp: "text-blue-500 bg-blue-500/10",
    k8s: "text-orange-500 bg-orange-500/10",
    skypilot: "text-cyan-500 bg-cyan-500/10",
}

// Provider descriptions
const providerDescriptions: Record<string, string> = {
    nosana: "Decentralized GPU Compute grid. Cheapest and fastest for inference.",
    akash: "Decentralized cloud compute. Open-source marketplace for GPUs.",
    aws: "Managed EC2 instances. High reliability, higher cost.",
    gcp: "Google Cloud Platform with SkyPilot. Unified multi-cloud orchestration.",
    k8s: "On-premises Kubernetes cluster. Full control and privacy.",
    skypilot: "Multi-cloud orchestration. Unified interface for AWS/GCP/Azure.",
}

// GCP regions for SkyPilot
const gcpRegions = [
    { id: "us-central1", name: "Iowa (us-central1)", available: true },
    { id: "us-east1", name: "South Carolina (us-east1)", available: true },
    { id: "us-west1", name: "Oregon (us-west1)", available: true },
    { id: "europe-west1", name: "Belgium (europe-west1)", available: true },
    { id: "europe-west4", name: "Netherlands (europe-west4)", available: true },
    { id: "asia-east1", name: "Taiwan (asia-east1)", available: true },
    { id: "asia-southeast1", name: "Singapore (asia-southeast1)", available: true },
]

// GPU types for GCP/SkyPilot
const gcpGpuTypes = [
    { gpu_type: "A100", gpu_memory_gb: 80, vcpu: 12, ram_gb: 85, description: "NVIDIA A100 80GB" },
    { gpu_type: "A10G", gpu_memory_gb: 24, vcpu: 4, ram_gb: 16, description: "NVIDIA A10G" },
    { gpu_type: "T4", gpu_memory_gb: 16, vcpu: 4, ram_gb: 16, description: "NVIDIA T4" },
    { gpu_type: "L4", gpu_memory_gb: 24, vcpu: 8, ram_gb: 32, description: "NVIDIA L4" },
    { gpu_type: "V100", gpu_memory_gb: 16, vcpu: 8, ram_gb: 61, description: "NVIDIA V100" },
    { gpu_type: "H100", gpu_memory_gb: 80, vcpu: 26, ram_gb: 200, description: "NVIDIA H100" },
]

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
    // New fields for SkyPilot/GCP
    selectedRegion: string;
    useSpot: boolean;
    isClusterProvider: boolean;
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
    | { type: "SET_LOADING_CREDENTIALS"; payload: boolean }
    | { type: "SET_REGION"; payload: string }
    | { type: "SET_USE_SPOT"; payload: boolean }
    | { type: "SET_CLUSTER_PROVIDER"; payload: boolean };

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
    selectedRegion: "",
    useSpot: false,
    isClusterProvider: false,
};

function poolReducer(state: NewPoolState, action: NewPoolAction): NewPoolState {
    switch (action.type) {
        case "SET_STEP": return { ...state, step: action.payload };
        case "SET_PROVIDER": return { ...state, selectedProvider: action.payload, selectedResource: null };
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
        case "SET_REGION": return { ...state, selectedRegion: action.payload };
        case "SET_USE_SPOT": return { ...state, useSpot: action.payload };
        case "SET_CLUSTER_PROVIDER": return { ...state, isClusterProvider: action.payload };
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
        loadingCredentials,
        selectedRegion,
        useSpot,
        isClusterProvider,
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
                    id: "gcp",
                    name: "Google Cloud (GCP)",
                    description: providerDescriptions.gcp,
                    icon: providerIcons.gcp,
                    color: providerColors.gcp,
                    category: "cloud",
                    clusterMode: true,
                    configPath: "/dashboard/settings/providers/cloud/gcp"
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
            clusterMode: data.capabilities?.supports_cluster_mode || false,
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
            case "gcp":
                return true; // GCP uses gcloud default credentials
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

    // Determine if selected provider is a cluster-based provider
    useEffect(() => {
        if (selectedProvider) {
            const provider = providers.find(p => p.id === selectedProvider);
            const isCluster = provider?.clusterMode || 
                provider?.capabilities?.supports_cluster_mode ||
                ["gcp", "aws", "azure", "lambda", "runpod"].includes(selectedProvider);
            dispatch({ type: "SET_CLUSTER_PROVIDER", payload: isCluster });
        }
    }, [selectedProvider, providers]);

    useEffect(() => {
        if (selectedProvider && step === 2) {
            const fetchResources = async () => {
                dispatch({ type: "SET_LOADING_RESOURCES", payload: true })
                try {
                    // For cluster providers, use predefined GPU types (no API call needed)
                    if (["gcp", "aws", "azure", "lambda", "runpod"].includes(selectedProvider)) {
                        dispatch({ type: "SET_RESOURCES", payload: [] })
                    } else {
                        const res = await computeApi.get(`/deployment/provider/resources?provider=${selectedProvider}`)
                        dispatch({ type: "SET_RESOURCES", payload: res.data.resources || [] })
                    }
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
            if (["nosana", "akash", "gcp", "skypilot"].includes(selectedProvider)) {
                void loadProviderCredentials()
            }
        }
    }, [selectedProvider, step])

    const handleCreate = async () => {
        if (!poolName) {
            toast.error("Please give your pool a name")
            return
        }

        // For cluster providers, validate region selection
        if (isClusterProvider && !selectedRegion) {
            toast.error("Please select a region")
            return
        }

        // For cluster providers, validate GPU selection
        if (isClusterProvider && !selectedResource) {
            toast.error("Please select a GPU type")
            return
        }

        const targetOrgId = user?.org_id || organizations?.[0]?.id;
        if (!targetOrgId) {
            toast.error("Organization context missing. Please reload.")
            return
        }

        dispatch({ type: "SET_CREATING", payload: true })

        try {
            // Build payload based on provider type
            const payload: any = {
                pool_name: poolName,
                owner_type: "user",
                owner_id: targetOrgId,
                provider: selectedProvider,
                is_dedicated: false,
                scheduling_policy_json: JSON.stringify({ strategy: "best_fit" })
            }

            if (isClusterProvider) {
                // Cluster-based provider (GCP/SkyPilot) - include region and spot settings
                payload.allowed_gpu_types = [selectedResource.gpu_type];
                payload.region_constraint = [selectedRegion];
                payload.use_spot = useSpot;
                // Estimate cost (for GCP, we don't have real-time pricing without API call)
                payload.max_cost_per_hour = estimateGcpCost(selectedResource.gpu_type, useSpot);
                payload.provider_pool_id = `${selectedRegion}/${selectedResource.gpu_type}`;
            } else {
                // Job-based provider (Nosana, Akash)
                payload.allowed_gpu_types = [selectedResource.gpu_type];
                payload.max_cost_per_hour = selectedResource.price_per_hour;
                payload.provider_pool_id = selectedResource.metadata?.market_address || selectedResource.provider_resource_id;
            }

            if (selectedCredential) {
                payload.provider_credential_name = selectedCredential
            }

            await computeApi.post("/deployment/createpool", payload)
            
            // For cluster providers, show different success message
            if (isClusterProvider) {
                toast.success(`Pool created! GPU cluster provisioning in ${selectedRegion}...`)
            } else {
                toast.success("Compute Pool created successfully!")
            }
            navigate("/dashboard/compute/pools")
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message
            toast.error(errorDetail)
            console.error(error)
        } finally {
            dispatch({ type: "SET_CREATING", payload: false })
        }
    }

    // Estimate GCP cost (rough approximation)
    const estimateGcpCost = (gpuType: string, isSpot: boolean): number => {
        const baseCosts: Record<string, number> = {
            "A100": 3.67,    // per hour
            "A10G": 0.77,
            "T4": 0.35,
            "L4": 0.55,
            "V100": 2.48,
            "H100": 4.50,
        };
        const base = baseCosts[gpuType] || 1.0;
        // Spot instances are ~60% cheaper
        return isSpot ? base * 0.4 : base;
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

            {/* Step 2: Configure Compute - Cluster Providers (GCP/SkyPilot) */}
            {step === 2 && isClusterProvider && (
                <div className="space-y-6">
                    <div className="p-6 rounded-xl border bg-slate-50 dark:bg-zinc-900/50 dark:border-zinc-800">
                        <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                            <Cloud className="w-5 h-5" />
                            {selectedProvider === 'gcp' ? 'Google Cloud Platform' : 'SkyPilot'} Configuration
                        </h3>
                        
                        {/* Region Selection */}
                        <div className="mb-6">
                            <label className="text-sm font-medium mb-2 block">Select Region</label>
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                {gcpRegions.map((region) => (
                                    <button
                                        key={region.id}
                                        onClick={() => dispatch({ type: "SET_REGION", payload: region.id })}
                                        className={cn(
                                            "p-3 rounded-lg border text-left text-sm transition-colors",
                                            selectedRegion === region.id
                                                ? "border-emerald-600 bg-emerald-50 dark:bg-emerald-900/20"
                                                : "border-slate-200 dark:border-zinc-700 hover:border-emerald-400"
                                        )}
                                    >
                                        <div className="font-medium">{region.name}</div>
                                        <div className="text-xs text-slate-500">{region.id}</div>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* GPU Selection */}
                        <div className="mb-6">
                            <label className="text-sm font-medium mb-2 block">Select GPU Type</label>
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                {gcpGpuTypes.map((gpu) => (
                                    <button
                                        key={gpu.gpu_type}
                                        onClick={() => dispatch({ type: "SET_RESOURCE", payload: { gpu_type: gpu.gpu_type, gpu_memory_gb: gpu.gpu_memory_gb, vcpu: gpu.vcpu, ram_gb: gpu.ram_gb, price_per_hour: estimateGcpCost(gpu.gpu_type, useSpot) }})}
                                        className={cn(
                                            "p-3 rounded-lg border text-left transition-colors",
                                            selectedResource?.gpu_type === gpu.gpu_type
                                                ? "border-emerald-600 bg-emerald-50 dark:bg-emerald-900/20"
                                                : "border-slate-200 dark:border-zinc-700 hover:border-emerald-400"
                                        )}
                                    >
                                        <div className="font-bold">{gpu.gpu_type}</div>
                                        <div className="text-xs text-slate-500">{gpu.gpu_memory_gb}GB VRAM</div>
                                        <div className="text-xs text-slate-400">{gpu.vcpu} vCPU</div>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Spot Toggle */}
                        <div className="p-4 rounded-lg border border-slate-200 dark:border-zinc-700 bg-white dark:bg-zinc-800">
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="font-medium">Use Spot Instances</div>
                                    <div className="text-xs text-slate-500">Up to 60% cheaper, but may be interrupted</div>
                                </div>
                                <button
                                    onClick={() => dispatch({ type: "SET_USE_SPOT", payload: !useSpot })}
                                    className={cn(
                                        "relative w-12 h-6 rounded-full transition-colors",
                                        useSpot ? "bg-emerald-600" : "bg-slate-300 dark:bg-zinc-600"
                                    )}
                                >
                                    <div className={cn(
                                        "absolute top-1 w-4 h-4 bg-white rounded-full transition-transform",
                                        useSpot ? "translate-x-7" : "translate-x-1"
                                    )} />
                                </button>
                            </div>
                            {useSpot && (
                                <div className="mt-2 text-xs text-emerald-600">
                                    Estimated cost: ~${estimateGcpCost(selectedResource?.gpu_type || 'A100', true).toFixed(2)}/hr (60% savings)
                                </div>
                            )}
                        </div>

                        {/* Summary */}
                        {selectedRegion && selectedResource && (
                            <div className="mt-4 p-4 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800">
                                <div className="text-sm font-medium text-emerald-800 dark:text-emerald-200">
                                    Summary: {selectedResource.gpu_type} in {selectedRegion}
                                    {useSpot && " (Spot)"}
                                </div>
                                <div className="text-xs text-emerald-600 dark:text-emerald-400">
                                    Estimated: ${estimateGcpCost(selectedResource.gpu_type, useSpot).toFixed(2)}/hr
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="flex justify-between pt-6">
                        <button
                            onClick={() => dispatch({ type: "SET_STEP", payload: 1 })}
                            className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200"
                        >
                            Back
                        </button>
                        <button
                            onClick={() => selectedRegion && selectedResource && dispatch({ type: "SET_STEP", payload: 3 })}
                            disabled={!selectedRegion || !selectedResource}
                            className="px-6 py-2 bg-emerald-600 text-white rounded-md text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Continue
                        </button>
                    </div>
                </div>
            )}

            {/* Step 2: Configure Compute - Job Providers (Nosana, Akash) */}
            {step === 2 && !isClusterProvider && (
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
                            className="px-6 py-2 bg-emerald-600 text-white rounded-md text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed"
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
                                className="w-full px-3 py-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-700 focus:ring-2 focus:ring-emerald-500/20 outline-none dark:text-zinc-100"
                                placeholder={isClusterProvider ? "e.g. My GCP Production Pool" : "e.g. My Nosana Pool"}
                                value={poolName}
                                onChange={(e) => dispatch({ type: "SET_POOL_NAME", payload: e.target.value })}
                            />
                        </div>

                        <PoolDetails
                            providerName={providers.find(p => p.id === selectedProvider)?.name}
                            resource={selectedResource}
                            isClusterProvider={isClusterProvider}
                            region={selectedRegion}
                            useSpot={useSpot}
                        />

                        {/* Cluster-specific info */}
                        {isClusterProvider && (
                            <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60">
                                <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
                                    <Cloud className="w-4 h-4" />
                                    <span className="font-medium">Cluster-based provisioning</span>
                                </div>
                                <p className="text-xs text-slate-500 mt-1">
                                    A persistent GPU cluster will be created. Deployments run on the cluster and can be started/stopped without recreating infrastructure.
                                </p>
                            </div>
                        )}

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
                            disabled={isCreating || !poolName}
                            className="flex-[2] px-6 py-2 bg-emerald-600 text-white rounded-md text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-2"
                        >
                            {isCreating ? (
                                <>Creating Pool...</>
                            ) : (
                                <>{isClusterProvider ? <><Cloud className="w-4 h-4" /> Create GPU Cluster</> : <><Zap className="w-4 h-4" /> Create Pool</>}</>
                            )}
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
                    <div className={cn("flex items-center gap-2", currentStep >= s && "text-emerald-600 dark:text-emerald-400")}>
                        <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-colors", currentStep >= s ? "bg-emerald-600 text-white border-emerald-600 dark:border-emerald-500 dark:bg-emerald-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800")}>{s}</div>
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
                    "text-left group relative p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:border-emerald-500/50 dark:hover:border-emerald-500/50 transition-colors hover:shadow-md flex flex-col gap-4",
                    p.disabled && "opacity-50 cursor-not-allowed hover:border-slate-200 dark:hover:border-zinc-800 hover:shadow-none bg-slate-50 dark:bg-zinc-900/50"
                )}
            >
                <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center transition-colors", p.color)}>
                    <p.icon className="w-6 h-6" />
                </div>
                <div>
                    <h3 className="font-bold text-lg mb-1 group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition-colors uppercase tracing-tight text-xs">{p.name}</h3>
                    <p className="text-sm text-slate-500 dark:text-zinc-400 leading-relaxed">{p.description}</p>
                    {p.capabilities && (
                        <div className="mt-2 flex flex-wrap gap-1">
                            {p.capabilities.is_ephemeral && <span className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">Ephemeral</span>}
                            {p.capabilities.pricing_model !== 'fixed' && <span className="text-[10px] bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded capitalize">{p.capabilities.pricing_model}</span>}
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
            className="text-left group relative p-6 rounded-xl border border-dashed border-slate-300 dark:border-zinc-800 bg-slate-50/30 dark:bg-zinc-900/20 hover:border-slate-400 dark:hover:border-zinc-700 transition-colors flex flex-col gap-4"
        >
            <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center opacity-40 grayscale", p.color)}>
                <p.icon className="w-6 h-6" />
            </div>
            <div>
                <h3 className="font-bold text-lg mb-1 text-slate-400 dark:text-zinc-500">{p.name}</h3>
                <p className="text-xs text-slate-400 dark:text-zinc-600">Configuration required to create pools on this network.</p>
            </div>
            <div className="mt-auto flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 text-xs font-bold uppercase tracking-wider opacity-0 group-hover:opacity-100 transition-opacity">
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
                    name="gpuSearch"
                    placeholder="Search GPUs (v100, t4, a100)…"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    autoComplete="off"
                    className="w-full pl-10 pr-4 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500/20 transition-colors text-sm"
                />
            </div>
            <select value={minVram} onChange={(e) => setMinVram(Number(e.target.value))} className="px-3 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg text-sm outline-none focus:ring-2 focus:ring-emerald-500/20">
                <option value={0}>All Memory</option>
                <option value={8}>8GB+ VRAM</option>
                <option value={16}>16GB+ VRAM</option>
                <option value={24}>24GB+ VRAM</option>
                <option value={40}>40GB+ VRAM</option>
                <option value={80}>80GB+ VRAM</option>
            </select>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as any)} className="px-3 py-2 bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded-lg text-sm outline-none focus:ring-2 focus:ring-emerald-500/20">
                <option value="price_asc">Price: Low to High</option>
                <option value="price_desc">Price: High to Low</option>
                <option value="memory">Memory: High to Low</option>
            </select>
        </div>
    )
}

function ResourceCard({ resource: res, isSelected, onSelect }: { resource: any, isSelected: boolean, onSelect: (r: any) => void }) {
    return (
        <button
            type="button"
            aria-pressed={isSelected}
            onClick={() => onSelect(res)}
            className={cn(
                "w-full cursor-pointer p-4 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 transition-colors relative text-left",
                isSelected ? "border-emerald-600 dark:border-emerald-500 ring-1 ring-emerald-600 dark:ring-emerald-500 shadow-sm" : "hover:border-emerald-400/30 dark:hover:border-emerald-600/30"
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
            {res.pricing_model && res.pricing_model !== 'fixed' && <div className="mt-1"><span className="text-[10px] bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded capitalize">{res.pricing_model}</span></div>}
            {isSelected && <div className="absolute top-4 right-4 w-5 h-5 bg-emerald-600 text-white rounded-full flex items-center justify-center"><Check className="w-3 h-3" /></div>}
        </button>
    )
}

function PoolDetails({ providerName, resource, isClusterProvider, region, useSpot }: { 
    providerName?: string, 
    resource: any,
    isClusterProvider?: boolean,
    region?: string,
    useSpot?: boolean
}) {
    return (
        <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60 space-y-3">
            <div className="flex justify-between text-sm">
                <span className="text-slate-500 dark:text-zinc-400">Provider</span>
                <span className="font-medium capitalize">{providerName}</span>
            </div>
            
            {isClusterProvider ? (
                <>
                    <div className="flex justify-between text-sm">
                        <span className="text-slate-500 dark:text-zinc-400">Region</span>
                        <span className="font-medium">{region || 'N/A'}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                        <span className="text-slate-500 dark:text-zinc-400">GPU Type</span>
                        <span className="font-medium">{resource?.gpu_type || 'N/A'}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                        <span className="text-slate-500 dark:text-zinc-400">Instance Type</span>
                        <span className="font-medium capitalize">{useSpot ? 'Spot' : 'On-demand'}</span>
                    </div>
                </>
            ) : (
                <>
                    <div className="flex justify-between text-sm">
                        <span className="text-slate-500 dark:text-zinc-400">GPU Type</span>
                        <span className="font-medium">{resource?.gpu_type}</span>
                    </div>
                    {resource?.pricing_model && resource?.pricing_model !== 'fixed' && (
                        <div className="flex justify-between text-sm">
                            <span className="text-slate-500 dark:text-zinc-400">Pricing Model</span>
                            <span className="font-medium capitalize">{resource?.pricing_model}</span>
                        </div>
                    )}
                </>
            )}
            
            <div className="flex justify-between text-sm">
                <span className="text-slate-500 dark:text-zinc-400">Est. Cost per Hour</span>
                <span className="font-medium">${resource?.price_per_hour || '0.00'}</span>
            </div>
        </div>
    )
}

function CredentialSelection({ provider, credentials, selectedCredential, setSelectedCredential, loading }: any) {
    return (
        <div className="pt-4 border-t border-slate-200/60 dark:border-zinc-800/60 space-y-3">
            <div className="space-y-2">
                <label htmlFor="credential-select" className="text-sm font-medium flex items-center gap-2"><Key className="w-4 h-4" /> {provider === "nosana" ? "Nosana API Key" : provider === "akash" ? "Akash Wallet" : "Provider Credential"}</label>
                {loading ? <div className="text-sm text-muted-foreground">Loading credentials...</div> : (
                    <select id="credential-select" value={selectedCredential} onChange={(e) => setSelectedCredential(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-700 focus:ring-2 focus:ring-emerald-500/20 outline-none dark:text-zinc-100 text-sm">
                        <option value="">Select a credential…</option>
                        {credentials.filter((key: any) => key.is_active).map((key: any) => <option key={key.name} value={key.name}>{key.name}</option>)}
                    </select>
                )}
                <p className="text-xs text-muted-foreground">Choose which credential to use for this pool</p>
            </div>
        </div>
    )
}
