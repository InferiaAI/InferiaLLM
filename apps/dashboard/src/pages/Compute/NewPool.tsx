import { useReducer, useEffect, useMemo, useState } from "react"
import { Cpu, Server, Check, Zap, Globe, ArrowRight, Search, Key, Cloud, HardDrive, Copy, CheckCircle2, X } from "lucide-react"
import { toast } from "sonner"
import { useNavigate, Link } from "react-router-dom"
import { cn } from "@/lib/utils"
import { useAuth } from "@/context/AuthContext"
import { computeApi } from "@/lib/api"
import { useQuery } from "@tanstack/react-query"
import { ConfigService, type NosanaApiKeyResponse } from "@/services/configService"
import { addWorkerNode, type AddWorkerNodeResponse } from "@/services/nodeService"
import { useInstanceCatalog, type InstanceType } from "@/hooks/useInstanceCatalog"
import { InstanceDropdown } from "@/components/compute/InstanceDropdown"

// Provider icons mapping
const providerIcons: Record<string, React.ComponentType<{ className?: string }>> = {
    nosana: Globe,
    akash: Cpu,
    aws: Server,
    gcp: Cloud,
    k8s: Server,
    worker: HardDrive,
}

// Provider color mapping
const providerColors: Record<string, string> = {
    nosana: "text-green-500 bg-green-500/10",
    akash: "text-purple-500 bg-purple-500/10",
    aws: "text-ember-500 bg-ember-500/10",
    gcp: "text-blue-500 bg-blue-500/10",
    k8s: "text-orange-500 bg-orange-500/10",
    worker: "text-ember-500 bg-ember-500/10",
}

// Provider descriptions
const providerDescriptions: Record<string, string> = {
    nosana: "Decentralized GPU Compute grid. Cheapest and fastest for inference.",
    akash: "Decentralized cloud compute. Open-source marketplace for GPUs.",
    aws: "Managed EC2 instances. High reliability, higher cost.",
    gcp: "Google Cloud Platform with Pulumi. Unified multi-cloud orchestration.",
    k8s: "On-premises Kubernetes cluster. Full control and privacy.",
    worker: "Self-hosted GPU hosts running the inferia-worker agent. Bare-metal, your own server, or a cloud VM you spin up. After creating the pool, click 'Add Worker' to register hosts.",
}

// GCP regions for Pulumi-managed clusters
const gcpRegions = [
    { id: "us-central1", name: "Iowa (us-central1)", available: true },
    { id: "us-east1", name: "South Carolina (us-east1)", available: true },
    { id: "us-west1", name: "Oregon (us-west1)", available: true },
    { id: "europe-west1", name: "Belgium (europe-west1)", available: true },
    { id: "europe-west4", name: "Netherlands (europe-west4)", available: true },
    { id: "asia-east1", name: "Taiwan (asia-east1)", available: true },
    { id: "asia-southeast1", name: "Singapore (asia-southeast1)", available: true },
]

// AWS regions for Pulumi-managed clusters. AWS region codes carry a SECOND
// hyphen (us-east-1), unlike GCP (us-east1). Sending a GCP code as the AWS
// region makes boto3 build an endpoint for a nonexistent region and
// provisioning fails at preflight with EndpointConnectionError, so the AWS
// pool form MUST offer these, not gcpRegions.
const awsRegions = [
    { id: "us-east-1", name: "N. Virginia (us-east-1)", available: true },
    { id: "us-east-2", name: "Ohio (us-east-2)", available: true },
    { id: "us-west-1", name: "N. California (us-west-1)", available: true },
    { id: "us-west-2", name: "Oregon (us-west-2)", available: true },
    { id: "eu-west-1", name: "Ireland (eu-west-1)", available: true },
    { id: "eu-west-2", name: "London (eu-west-2)", available: true },
    { id: "eu-central-1", name: "Frankfurt (eu-central-1)", available: true },
    { id: "ap-south-1", name: "Mumbai (ap-south-1)", available: true },
    { id: "ap-southeast-1", name: "Singapore (ap-southeast-1)", available: true },
    { id: "ap-southeast-2", name: "Sydney (ap-southeast-2)", available: true },
    { id: "ap-northeast-1", name: "Tokyo (ap-northeast-1)", available: true },
    { id: "ca-central-1", name: "Canada (ca-central-1)", available: true },
]

// GPU types for GCP/Pulumi
const gcpGpuTypes = [
    { gpu_type: "H100", gpu_memory_gb: 80, vcpu: 26, ram_gb: 200, description: "NVIDIA H100 80GB" },
    { gpu_type: "A100", gpu_memory_gb: 80, vcpu: 12, ram_gb: 85, description: "NVIDIA A100 80GB" },
    { gpu_type: "L4", gpu_memory_gb: 24, vcpu: 8, ram_gb: 32, description: "NVIDIA L4" },
    { gpu_type: "T4", gpu_memory_gb: 16, vcpu: 4, ram_gb: 16, description: "NVIDIA T4" },
    { gpu_type: "V100", gpu_memory_gb: 16, vcpu: 8, ram_gb: 61, description: "NVIDIA V100 16GB" },
]

// AWS instance catalog is fetched at runtime from the orchestration
// service (see hooks/useInstanceCatalog.ts). Adding a new EC2 type is
// a one-file change in the backend catalog module — no frontend edit
// needed. The catalog groups by class:
//   - normal_gpu  → single-GPU instances for routine inference
//   - heavy_gpu   → multi-GPU and high-end (A100/H100/H200) instances
//   - cpu         → no-GPU instances for control-plane / cheap test pools
// The InstanceDropdown renders a GPU-first flat list (heavy → normal → cpu)
// so no tier tabs are needed. The spot toggle applies a 0.4x multiplier
// identical to estimateGcpCost's discount factor.
type InstanceTier = "normal_gpu" | "heavy_gpu" | "cpu";

/**
 * Translate a catalog row (from useInstanceCatalog) into the
 * legacy `selectedResource` shape the rest of NewPool consumes
 * (payload construction, cost summary, gcpFallback). Keeping the
 * shape stable avoids touching every downstream usage just to
 * rename a field.
 */
function catalogRowToSelectedResource(inst: InstanceType) {
    return {
        gpu_type: inst.gpu_model || "(none)",
        gpu_memory_gb: inst.gpu_ram_gb,
        vcpu: inst.vcpu,
        ram_gb: inst.ram_gb,
        price_per_hour: inst.price_per_hour,
        provider_resource_id: inst.name,
    };
}

/**
 * Compute the hourly cost for the cluster-provider Step 2 summary + the
 * Spot toggle's "savings" line.
 *
 * - AWS entries (mapped from useInstanceCatalog rows via
 *   catalogRowToSelectedResource) always carry an explicit
 *   `price_per_hour`. We use that directly and apply the same 0.4x
 *   spot multiplier estimateGcpCost uses, so the savings figure stays
 *   consistent between paths. Multiplying by gpuCount is a no-op for
 *   tiers that already represent the whole multi-GPU instance (p4d/p5),
 *   so the GPU Count selector is preserved on heavy AND clamped to 1
 *   on CPU via the reducer.
 * - GCP / Azure entries don't have `price_per_hour` set (the static
 *   gcpGpuTypes only declares the GPU). They fall back to the
 *   semantic-name lookup table inside estimateGcpCost.
 */
function computeHourlyCost(
    resource: { price_per_hour?: number; gpu_type?: string } | null | undefined,
    isSpot: boolean,
    gpuCount: number,
    gcpFallback: (gpuType: string, isSpot: boolean) => number,
): number {
    if (!resource) return 0;
    const safeCount = gpuCount > 0 ? gpuCount : 1;
    if (typeof resource.price_per_hour === "number") {
        const spotMul = isSpot ? 0.4 : 1.0;
        return resource.price_per_hour * spotMul * safeCount;
    }
    return gcpFallback(resource.gpu_type || "A100", isSpot) * safeCount;
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
    // Vendor filter chip group above the resource grid:
    //   "all"     — show everything (GPU + CPU-only)
    //   "nvidia"  — NVIDIA-branded GPUs only (default)
    //   "other"   — non-NVIDIA GPUs (AMD, Intel/Habana, ...)
    //   "none"    — CPU-only instances (no GPU)
    gpuVendorFilter: "all" | "nvidia" | "other" | "none";
    providerCredentials: NosanaApiKeyResponse[];
    selectedCredential: string;
    loadingCredentials: boolean;
    // New fields for Pulumi/GCP cluster provisioning
    selectedRegion: string;
    useSpot: boolean;
    isClusterProvider: boolean;
    gpuCount: number;
    // AWS instance tier selector (Step 2, cluster path, AWS only).
    // Switching the tier clears any selectedResource — same precedent as
    // the gpu vendor filter chips (`SET_GPU_VENDOR_FILTER`).
    instanceTier: InstanceTier;
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
    | { type: "SET_CLUSTER_PROVIDER"; payload: boolean }
    | { type: "SET_GPU_COUNT"; payload: number }
    | { type: "SET_GPU_VENDOR_FILTER"; payload: NewPoolState["gpuVendorFilter"] }
    | { type: "SET_INSTANCE_TIER"; payload: InstanceTier };

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
    gpuVendorFilter: "nvidia",
    providerCredentials: [],
    selectedCredential: "",
    loadingCredentials: false,
    selectedRegion: "",
    useSpot: false,
    isClusterProvider: false,
    gpuCount: 1,
    instanceTier: "normal_gpu",
};

function poolReducer(state: NewPoolState, action: NewPoolAction): NewPoolState {
    switch (action.type) {
        case "SET_STEP": return { ...state, step: action.payload };
        case "SET_PROVIDER": return { ...state, selectedProvider: action.payload, selectedResource: null };
        case "SET_RESOURCE": return {
            ...state,
            selectedResource: action.payload,
            gpuCount: action.payload?.gpu_type === "(none)" ? 1 : state.gpuCount,
        };
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
        case "SET_GPU_COUNT": return { ...state, gpuCount: action.payload };
        case "SET_GPU_VENDOR_FILTER": return { ...state, gpuVendorFilter: action.payload, selectedResource: null };
        // Switching tiers clears selectedResource (same precedent as
        // SET_GPU_VENDOR_FILTER) and force-resets gpuCount to 1 when the
        // user moves to CPU (no GPU to count). Heavy/Normal preserve the
        // existing gpuCount so a user toggling between them keeps their
        // multi-GPU choice.
        case "SET_INSTANCE_TIER": return {
            ...state,
            instanceTier: action.payload,
            selectedResource: null,
            gpuCount: action.payload === "cpu" ? 1 : state.gpuCount,
        };
        default: return state;
    }
}

export default function NewPool() {
    const navigate = useNavigate()
    const { user, organizations } = useAuth()
    const [state, dispatch] = useReducer(poolReducer, initialState);
    const [workerResult, setWorkerResult] = useState<AddWorkerNodeResponse | null>(null);
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
        gpuCount,
        gpuVendorFilter,
    } = state;

    // Fetch provider configuration
    const { data: config, isLoading: loadingConfig } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    // Live AWS instance catalog. Replaces the previously hard-coded
    // awsInstanceTiers constant — adding a new EC2 type is now a backend
    // catalog change with no frontend edit. The fetch is cheap and the
    // hook caches for 5 minutes, so this is safe to call unconditionally
    // (the catalog block is only rendered when selectedProvider === 'aws'
    // anyway). Region-aware: when a region is selected the hook tries the
    // live discover endpoint for that region (with curated fallback).
    const { data: awsCatalog, isLoading: loadingAwsCatalog } = useInstanceCatalog(
        selectedProvider === "aws" ? selectedRegion : undefined,
    )

    // Fetch live AWS regions from the orchestration service. Falls back to
    // the static awsRegions list when the endpoint is unavailable or returns
    // fallback:true. Enabled only when the user is on the AWS path to avoid
    // unnecessary requests on GCP / worker pools.
    const { data: liveRegions } = useQuery({
        queryKey: ["aws-regions"],
        queryFn: () => ConfigService.listAwsRegions(),
        staleTime: 60 * 60 * 1000,
        enabled: selectedProvider === "aws",
    })

    // Compute the region option list: prefer live regions when available and
    // not a fallback response, else fall back to the static awsRegions array.
    // For known region IDs we preserve the human-readable name from the static
    // list; unknown IDs (returned only by live discovery) use the raw id as
    // the display name.
    const awsRegionOptions =
        liveRegions && !liveRegions.fallback && liveRegions.regions.length
            ? liveRegions.regions.map((id) => {
                const known = awsRegions.find((r) => r.id === id);
                return { id, name: known ? known.name : id, available: true };
              })
            : awsRegions;

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
                    id: "worker",
                    name: "Self-hosted (inferia-worker)",
                    description: providerDescriptions.worker,
                    icon: providerIcons.worker,
                    color: providerColors.worker,
                    recommended: true,
                    category: "self_hosted",
                    configPath: "",
                    capabilities: undefined,
                    clusterMode: false,
                },
                {
                    id: "nosana",
                    name: "Nosana Network",
                    description: providerDescriptions.nosana,
                    icon: providerIcons.nosana,
                    color: providerColors.nosana,
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
                    category: "cloud",
                    clusterMode: true,
                    configPath: "/dashboard/settings/providers/cloud/aws"
                }
            ]
        }

        // Build from the API list, but:
        //  * skip 'on_prem' — it's only a server-side adapter alias for
        //    'worker' so createpool accepts the DB enum value; the UI must
        //    never show it as a separate card.
        //  * keep 'worker' overrides (name + description + icon) so the
        //    card matches the static fallback ("Self-hosted (inferia-worker)")
        //    rather than the generic "Worker Network" auto-title.
        const apiList = Object.entries(providersData)
            .filter(([id]) => id !== "on_prem")
            .map(([id, data]: [string, any]) => {
                const isWorker = id === "worker";
                return {
                    id,
                    name: isWorker
                        ? "Self-hosted (inferia-worker)"
                        : `${id.charAt(0).toUpperCase() + id.slice(1)} Network`,
                    description: providerDescriptions[id] || `${id} compute provider`,
                    icon: providerIcons[id] || Server,
                    color: providerColors[id] || "text-muted-foreground bg-muted-foreground/10",
                    category: isWorker ? "self_hosted" : (data.adapter_type || "cloud"),
                    // Worker pools never use the credentials-editor page.
                    configPath: isWorker
                        ? ""
                        : `/dashboard/settings/providers/${data.adapter_type || 'cloud'}/${id}`,
                    capabilities: data.capabilities,
                    clusterMode: data.capabilities?.supports_cluster_mode || false,
                    recommended: isWorker || (data.adapter_type === 'depin' && id === 'nosana'),
                };
            });
        // Ensure worker is present even when the backend hides it.
        if (!apiList.some((p) => p.id === "worker")) {
            apiList.unshift({
                id: "worker",
                name: "Self-hosted (inferia-worker)",
                description: providerDescriptions.worker,
                icon: providerIcons.worker,
                color: providerColors.worker,
                category: "self_hosted",
                configPath: "",
                capabilities: undefined,
                clusterMode: false,
                recommended: true,
            });
        }
        return apiList;
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
                return !!(cloud.gcp?.project_id || cloud.gcp?.service_account_json);
            case "aws":
                return !!cloud.aws?.access_key_id;
            case "k8s":
                return true;
            case "worker":
            case "on_prem":
                // The self-hosted (inferia-worker) provider has no credentials
                // to configure — workers register themselves with a bootstrap
                // token issued from the pool detail page after creation.
                // 'on_prem' is a server-side alias we strip from the list
                // above, but include it here for defence-in-depth.
                return true;
            default:
                return !!(depin[pid] || cloud[pid]);
        }
    };

    const providers = useMemo(() => providerMeta.map(p => ({
        ...p,
        isConfigured: isProviderConfigured(p.id)
    })), [providerMeta, config]);

    // Determine if selected provider is a cluster-based provider.
    // AWS is provisioned by Pulumi but its catalog comes from the live
    // describe_instance_types call, so it goes through the resource-card
    // UI (with the GPU-vendor filter chips) — NOT the static GCP cluster
    // UI. Only GCP/Azure/Lambda/Runpod still fall back to the static path.
    useEffect(() => {
        if (selectedProvider) {
            const provider = providers.find(p => p.id === selectedProvider);
            const isCluster = provider?.clusterMode ||
                provider?.capabilities?.supports_cluster_mode ||
                ["gcp", "azure", "lambda", "runpod"].includes(selectedProvider);
            dispatch({ type: "SET_CLUSTER_PROVIDER", payload: isCluster });
        }
    }, [selectedProvider, providers]);

    useEffect(() => {
        if (selectedProvider && step === 2) {
            // Self-hosted (inferia-worker) pools have no DePIN/cloud
            // resources to enumerate — the GPU comes from the worker itself
            // at registration time.
            if (selectedProvider === "worker") {
                dispatch({ type: "SET_RESOURCES", payload: [] });
                dispatch({ type: "SET_LOADING_RESOURCES", payload: false });
                return;
            }
            const fetchResources = async () => {
                dispatch({ type: "SET_LOADING_RESOURCES", payload: true })
                try {
                    // For static-cluster providers (GCP/Azure/Lambda/Runpod via
                    // Pulumi), the UI uses the gcpGpuTypes catalog so no
                    // network call is needed. AWS, Nosana, Akash all hit the
                    // resource endpoint for a live catalog.
                    if (["gcp", "azure", "lambda", "runpod"].includes(selectedProvider)) {
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
            if (["nosana", "akash", "gcp"].includes(selectedProvider)) {
                void loadProviderCredentials()
            }
        }
    }, [selectedProvider, step])

    const handleCreate = async () => {
        if (!poolName) {
            toast.error("Please give your pool a name")
            return
        }

        // For AWS pools a region is mandatory — the backend 422s without it.
        if (selectedProvider === "aws" && !selectedRegion) {
            toast.error("Select a region for the AWS pool")
            return
        }

        // For other cluster providers, validate region selection
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
            // Self-hosted (inferia-worker) takes the node-centric path: the
            // node is added to the org's hidden default pool and the
            // endpoint returns the bootstrap env_snippet to paste into a
            // GPU host's compose file.
            if (selectedProvider === "worker") {
                const r = await addWorkerNode({
                    node_name: poolName,
                    labels: {},
                });
                setWorkerResult(r);
                toast.success("Worker node created — copy the .env snippet below.");
                return;
            }

            // Build payload based on provider type
            const isWorkerPool = selectedProvider === "worker";
            const payload: any = {
                pool_name: poolName,
                owner_type: "user",
                owner_id: targetOrgId,
                // Self-hosted pools map to the existing 'on_prem' provider
                // enum in compute_pools; the agent_kind='worker' column on
                // compute_inventory is what distinguishes worker nodes.
                provider: isWorkerPool ? "on_prem" : selectedProvider,
                is_dedicated: false,
                scheduling_policy_json: JSON.stringify({ strategy: "best_fit" })
            }

            if (isWorkerPool) {
                // No pre-selected resource: workers contribute their GPU at
                // registration time. Send an "any-GPU" hint so other parts
                // of the system don't reject this pool for missing fields.
                payload.allowed_gpu_types = ["any"];
                payload.max_cost_per_hour = 0;
                payload.provider_pool_id = `worker:${poolName}`;
            } else if (selectedProvider === "aws" && isClusterProvider) {
                // AWS via Pulumi — allowed_gpu_types[0] must be the EC2
                // instance type (e.g. "g6.xlarge"), NOT the semantic GPU
                // name ("L4"). The catalog rows from useInstanceCatalog
                // populate provider_resource_id via
                // catalogRowToSelectedResource, so the first branch
                // (price_per_hour set) always wins. The gpu_type fallback
                // remains for forwards-compatibility with any other
                // selectedResource shape that might land here.
                //
                // The provider_resource_id is ALWAYS set on the selected
                // card — whether it's "c6i.xlarge" (cpu), "g6.xlarge"
                // (normal_gpu), or "p5.48xlarge" (heavy_gpu). All three
                // values flow through this branch unchanged: the backend
                // receives the EC2 instance type and Pulumi launches it.
                // No special-casing of the CPU tier is needed because
                // selectedResource.price_per_hour is always populated
                // from the catalog row's price_per_hour.
                const instanceType =
                    selectedResource.provider_resource_id || selectedResource.gpu_type;
                payload.allowed_gpu_types = [instanceType];
                payload.region_constraint = [selectedRegion];
                payload.use_spot = useSpot;
                payload.gpu_count = gpuCount;
                payload.max_cost_per_hour =
                    selectedResource.price_per_hour ||
                    estimateGcpCost(selectedResource.gpu_type, useSpot) * gpuCount;
                payload.provider_pool_id = `aws/${instanceType}`;
            } else if (isClusterProvider) {
                // GCP / Azure via Pulumi — semantic gpu_type stays meaningful.
                payload.allowed_gpu_types = [selectedResource.gpu_type];
                payload.region_constraint = [selectedRegion];
                payload.use_spot = useSpot;
                payload.gpu_count = gpuCount;
                payload.max_cost_per_hour = estimateGcpCost(selectedResource.gpu_type, useSpot) * gpuCount;
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

            // AWS provisioning configuration (subnet/SG/AMI/IAM/root/image-tag)
            // is now managed account-wide under Settings → Providers → AWS,
            // not per-pool. Pulumi reads those defaults via the credential.

            const createRes = await computeApi.post("/deployment/createpool", payload)
            const newPoolId = createRes?.data?.pool_id || createRes?.data?.id;

            toast.success("Pool created");
            navigate(
                newPoolId
                    ? `/dashboard/compute/pools/${newPoolId}`
                    : "/dashboard/compute/pools",
            );
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
        <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-foreground">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Create New Compute Node</h2>
                <p className="text-muted-foreground mt-2">
                    Add a compute node so deployments can land on it. Each provider registers exactly one node per submission.
                </p>
            </div>

            {/* Progress Steps */}
            <StepProgress currentStep={step} />

            {/* Step 1: Provider Selection.
                Only show providers whose credentials are configured in
                Settings → Providers. The 'worker' / 'k8s' providers don't
                require credentials so they always show; everything else
                needs an entry in ProvidersConfig. */}
            {step === 1 && (() => {
                const eligible = providers.filter(
                    (p) => p.isConfigured || p.id === "worker" || p.id === "on_prem"
                );
                if (eligible.length === 0) {
                    return (
                        <div className="p-12 text-center border rounded-lg border-dashed bg-muted/40 dark:bg-card/40">
                            <p className="text-sm font-medium mb-2">
                                No provider configured yet
                            </p>
                            <p className="text-xs text-muted-foreground mb-4">
                                To create a compute node, first add credentials for at
                                least one provider in <strong>Settings → Providers</strong>.
                                The self-hosted <em>inferia-worker</em> path is always
                                available with no credentials.
                            </p>
                            <Link
                                to="/dashboard/settings/providers"
                                className="inline-flex items-center gap-1.5 px-4 py-2 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700"
                            >
                                Go to Providers
                                <ArrowRight className="w-4 h-4" />
                            </Link>
                        </div>
                    );
                }
                return (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        {eligible.map((p) => (
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
                );
            })()}

            {/* Step 2: Self-hosted (inferia-worker) — just name the pool. */}
            {step === 2 && selectedProvider === "worker" && (
                <div className="space-y-6">
                    <div className="p-6 rounded-xl border bg-muted dark:bg-card/50 dark:border-border">
                        <h3 className="text-lg font-semibold mb-1 flex items-center gap-2">
                            <HardDrive className="w-5 h-5 text-ember-500" />
                            Self-hosted (inferia-worker) pool
                        </h3>
                        <p className="text-sm text-muted-foreground mb-6">
                            Workers contribute their own GPU at registration time, so this
                            step only asks for a pool name. After the pool is created
                            you'll land on the Workers tab where you can generate an
                            <span className="font-mono"> .env </span> snippet for each GPU
                            host and run <span className="font-mono">docker compose up</span>.
                        </p>

                        <div className="space-y-2">
                            <label htmlFor="worker-pool-name" className="text-sm font-medium">
                                Pool name
                            </label>
                            <input
                                id="worker-pool-name"
                                type="text"
                                placeholder="e.g. dc1-gpus, lab-h100s"
                                value={poolName}
                                onChange={(e) => dispatch({ type: "SET_POOL_NAME", payload: e.target.value })}
                                className="h-10 w-full max-w-md rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
                                autoComplete="off"
                            />
                            <p className="text-xs text-muted-foreground">
                                Letters, digits, dashes, underscores. Visible to operators in the
                                pool list.
                            </p>
                        </div>

                        <div className="mt-6 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400">
                            Make sure your GPU host has Docker + (if you want GPU) the NVIDIA
                            Container Toolkit installed before clicking <span className="font-mono">Add Worker</span>.
                            Pool credentials are not needed — each worker registers with a
                            short-lived bootstrap token you mint from the pool's Workers tab.
                        </div>
                    </div>

                    <div className="flex items-center justify-between">
                        <button
                            onClick={() => dispatch({ type: "SET_STEP", payload: 1 })}
                            className="px-4 py-2 text-sm rounded-md border hover:bg-muted"
                        >
                            Back
                        </button>
                        <button
                            onClick={handleCreate}
                            disabled={isCreating || !poolName}
                            className={cn(
                                "px-4 py-2 text-sm rounded-md text-white inline-flex items-center gap-2",
                                isCreating || !poolName
                                    ? "bg-ember-600/60 cursor-not-allowed"
                                    : "bg-ember-600 hover:bg-ember-700",
                            )}
                        >
                            {isCreating ? "Creating…" : "Create pool"}
                            {!isCreating && <ArrowRight className="w-4 h-4" />}
                        </button>
                    </div>
                </div>
            )}

            {/* Step 2: Configure Compute - Cluster Providers (GCP/AWS/Azure via Pulumi) */}
            {step === 2 && isClusterProvider && (
                <div className="space-y-6">
                    <div className="p-6 rounded-xl border bg-muted dark:bg-card/50 dark:border-border">
                        <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                            <Cloud className="w-5 h-5" />
                            {selectedProvider === 'gcp' ? 'Google Cloud Platform' :
                             selectedProvider === 'aws' ? 'Amazon Web Services' :
                             selectedProvider === 'azure' ? 'Microsoft Azure' :
                             'Cluster'} Configuration
                        </h3>

                        {/* Instance Tier selector removed — instance type is
                            now selected via InstanceDropdown (GPU-first flat
                            list) further down. The tier state is still in the
                            reducer but no longer driven from UI here. */}

                        {selectedProvider === "aws" && (
                            <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-lg text-xs text-blue-700 dark:text-blue-200">
                                AWS provisioning details (subnet, security groups, AMI,
                                IAM profile, root volume, worker image tag) are configured
                                account-wide under <span className="font-semibold">Settings
                                → Providers → AWS</span>. Pulumi uses those defaults plus
                                the region and GPU type below.
                            </div>
                        )}

                        {/* Region Selection */}
                        <div className="mb-6">
                            <label className="text-sm font-medium mb-2 block">Select Region</label>
                            {selectedProvider === "aws" ? (
                                <select
                                    data-testid="aws-region-select"
                                    value={selectedRegion}
                                    onChange={(e) => dispatch({ type: "SET_REGION", payload: e.target.value })}
                                    className="w-full px-3 py-2 rounded-md border border-border bg-card text-sm outline-none focus:ring-2 focus:ring-ember-500/20 dark:text-cream"
                                >
                                    <option value="">Select a region…</option>
                                    {awsRegionOptions.map((r) => (
                                        <option key={r.id} value={r.id}>{r.name}</option>
                                    ))}
                                </select>
                            ) : (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                    {gcpRegions.map((region) => (
                                        <button
                                            key={region.id}
                                            onClick={() => dispatch({ type: "SET_REGION", payload: region.id })}
                                            className={cn(
                                                "p-3 rounded-lg border text-left text-sm transition-colors",
                                                selectedRegion === region.id
                                                    ? "border-ember-600 bg-ember-50 dark:bg-ember-900/20"
                                                    : "border-border hover:border-ember-400"
                                            )}
                                        >
                                            <div className="font-medium">{region.name}</div>
                                            <div className="text-xs text-muted-foreground">{region.id}</div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* GPU / Instance Selection.
                            AWS: flat GPU-first dropdown (heavy_gpu + normal_gpu
                            first, then cpu) via InstanceDropdown. GCP/Azure stay
                            on the existing gcpGpuTypes button grid. */}
                        <div className="mb-6">
                            <label className="text-sm font-medium mb-2 block">
                                {selectedProvider === "aws"
                                    ? "Select Instance Type"
                                    : "Select GPU Type"}
                            </label>
                            {selectedProvider === "aws" ? (() => {
                                // GPU-first: heavy → normal → cpu
                                const flatInstances = awsCatalog
                                    ? [
                                        ...awsCatalog.heavy_gpu,
                                        ...awsCatalog.normal_gpu,
                                        ...awsCatalog.cpu,
                                      ]
                                    : [];
                                return (
                                    <InstanceDropdown
                                        instances={flatInstances}
                                        value={selectedResource?.provider_resource_id ?? null}
                                        loading={loadingAwsCatalog && !awsCatalog}
                                        onSelect={(inst) =>
                                            dispatch({
                                                type: "SET_RESOURCE",
                                                payload: catalogRowToSelectedResource(inst),
                                            })
                                        }
                                    />
                                );
                            })() : (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                    {gcpGpuTypes.map((gpu) => (
                                        <button
                                            key={gpu.gpu_type}
                                            onClick={() => dispatch({ type: "SET_RESOURCE", payload: { gpu_type: gpu.gpu_type, gpu_memory_gb: gpu.gpu_memory_gb, vcpu: gpu.vcpu, ram_gb: gpu.ram_gb, price_per_hour: estimateGcpCost(gpu.gpu_type, useSpot) }})}
                                            className={cn(
                                                "p-3 rounded-lg border text-left transition-colors",
                                                selectedResource?.gpu_type === gpu.gpu_type
                                                    ? "border-ember-600 bg-ember-50 dark:bg-ember-900/20"
                                                    : "border-border hover:border-ember-400"
                                            )}
                                        >
                                            <div className="font-bold">{gpu.gpu_type}</div>
                                            <div className="text-xs text-muted-foreground">{gpu.gpu_memory_gb}GB VRAM</div>
                                            <div className="text-xs text-muted-foreground">{gpu.vcpu} vCPU</div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* GPU Count Selection — hidden when no GPU is selected
                            (AWS CPU instances have gpu_count=0). The reducer
                            already holds gpuCount=1 for CPU instances so the
                            submitted payload is sane regardless. */}
                        {!(selectedProvider === "aws" && selectedResource != null && selectedResource.gpu_type === "(none)") && (
                            <div className="mb-6" data-testid="gpu-count">
                                <label className="text-sm font-medium mb-2 block">Number of GPUs</label>
                                <div className="grid grid-cols-4 md:grid-cols-8 gap-3">
                                    {[1, 2, 4, 8].map((count) => (
                                        <button
                                            key={count}
                                            onClick={() => dispatch({ type: "SET_GPU_COUNT", payload: count })}
                                            className={cn(
                                                "p-3 rounded-lg border text-center font-bold transition-colors",
                                                gpuCount === count
                                                    ? "border-ember-600 bg-ember-50 dark:bg-ember-900/20"
                                                    : "border-border hover:border-ember-400"
                                            )}
                                        >
                                            {count}x
                                        </button>
                                    ))}
                                </div>
                                <p className="text-xs text-muted-foreground mt-1">
                                    {gpuCount > 1
                                        ? `${gpuCount} GPUs will be provisioned on a single node (multi-GPU).`
                                        : "Single GPU per node."}
                                </p>
                            </div>
                        )}

                        {/* Spot Toggle */}
                        <div className="p-4 rounded-lg border border-border bg-card">
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="font-medium">Use Spot Instances</div>
                                    <div className="text-xs text-muted-foreground">Up to 60% cheaper, but may be interrupted</div>
                                </div>
                                <button
                                    onClick={() => dispatch({ type: "SET_USE_SPOT", payload: !useSpot })}
                                    className={cn(
                                        "relative w-12 h-6 rounded-full transition-colors",
                                        useSpot ? "bg-ember-600" : "bg-muted dark:bg-card"
                                    )}
                                >
                                    <div className={cn(
                                        "absolute top-1 w-4 h-4 bg-card rounded-full transition-transform",
                                        useSpot ? "translate-x-7" : "translate-x-1"
                                    )} />
                                </button>
                            </div>
                            {useSpot && (
                                <div className="mt-2 text-xs text-ember-600">
                                    Estimated cost: ~${(computeHourlyCost(selectedResource, useSpot, gpuCount, estimateGcpCost)).toFixed(2)}/hr (60% savings)
                                </div>
                            )}
                        </div>

                        {/* Summary. For AWS entries (and any other case
                            where price_per_hour is set on the selected
                            resource) we use that directly — the spot
                            discount is applied multiplicatively to match
                            estimateGcpCost's 0.4x. GCP entries with no
                            explicit price still fall through to the
                            semantic-name lookup table. */}
                        {selectedRegion && selectedResource && (
                            <div className="mt-4 p-4 rounded-lg bg-ember-50 dark:bg-ember-900/20 border border-ember-200 dark:border-ember-800">
                                <div className="text-sm font-medium text-ember-800 dark:text-ember-200">
                                    Summary: {gpuCount}x{" "}
                                    {selectedProvider === "aws" && selectedResource.provider_resource_id
                                        ? selectedResource.provider_resource_id
                                        : selectedResource.gpu_type}
                                    {" "}in {selectedRegion}
                                    {useSpot && " (Spot)"}
                                </div>
                                <div className="text-xs text-ember-600 dark:text-ember-400">
                                    Estimated: ${computeHourlyCost(selectedResource, useSpot, gpuCount, estimateGcpCost).toFixed(2)}/hr
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="flex justify-between pt-6">
                        <button
                            onClick={() => dispatch({ type: "SET_STEP", payload: 1 })}
                            className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground dark:hover:text-cream/85"
                        >
                            Back
                        </button>
                        <button
                            onClick={() => selectedRegion && selectedResource && dispatch({ type: "SET_STEP", payload: 3 })}
                            disabled={!selectedRegion || !selectedResource}
                            className="px-6 py-2 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Continue
                        </button>
                    </div>
                </div>
            )}

            {/* Step 2: Configure Compute - Job Providers (Nosana, Akash, AWS) */}
            {step === 2 && !isClusterProvider && selectedProvider !== "worker" && (
                <div className="space-y-6">
                    {/* GPU vendor filter chips — only meaningful when the
                        provider's catalog actually carries a gpu_vendor field
                        (AWS does today; Nosana/Akash don't, so the chips
                        still render and "All" + "NVIDIA" both match because
                        unknown vendor counts as NVIDIA-by-default for those
                        existing providers). */}
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-muted-foreground mr-1">Filter:</span>
                        {([
                            ["all", "All"],
                            ["nvidia", "NVIDIA"],
                            ["other", "Other GPU"],
                            ["none", "No GPU"],
                        ] as const).map(([v, label]) => (
                            <button
                                key={v}
                                type="button"
                                onClick={() => dispatch({ type: "SET_GPU_VENDOR_FILTER", payload: v })}
                                className={cn(
                                    "px-3 py-1 rounded-full border text-xs font-medium transition-colors",
                                    gpuVendorFilter === v
                                        ? "border-ember-600 bg-ember-50 text-ember-700 dark:bg-ember-900/20 dark:text-ember-300"
                                        : "border-border hover:border-ember-400"
                                )}
                            >
                                {label}
                            </button>
                        ))}
                    </div>

                    <ResourceFilter
                        searchQuery={searchQuery}
                        setSearchQuery={(q) => dispatch({ type: "SET_SEARCH", payload: q })}
                        minVram={minVram}
                        setMinVram={(v) => dispatch({ type: "SET_VRAM", payload: v })}
                        sortBy={sortBy}
                        setSortBy={(s) => dispatch({ type: "SET_SORT", payload: s })}
                    />

                    {loadingResources ? (
                        <div className="text-center py-12 text-muted-foreground">Loading available resources...</div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {availableResources
                                .filter(res => {
                                    const matchesSearch = res.gpu_type.toLowerCase().includes(searchQuery.toLowerCase()) ||
                                        res.provider_resource_id.toLowerCase().includes(searchQuery.toLowerCase());
                                    const matchesVram = res.gpu_memory_gb >= minVram;
                                    // gpu_vendor is set by the AWS adapter; for legacy
                                    // providers (Nosana/Akash) the field is missing —
                                    // treat that as "nvidia" so existing flows are
                                    // unaffected when the chip is on its default.
                                    const vendor = (res as any).gpu_vendor || "nvidia";
                                    const matchesVendor =
                                        gpuVendorFilter === "all" ||
                                        (gpuVendorFilter === "nvidia" && vendor === "nvidia") ||
                                        (gpuVendorFilter === "other" && (vendor === "amd" || vendor === "intel" || vendor === "other")) ||
                                        (gpuVendorFilter === "none" && vendor === "none");
                                    return matchesSearch && matchesVram && matchesVendor;
                                })
                                .sort((a, b) => {
                                    if (sortBy === "price_asc") {
                                        const pa = a.price_per_hour ?? Infinity;
                                        const pb = b.price_per_hour ?? Infinity;
                                        return pa - pb;
                                    }
                                    if (sortBy === "price_desc") {
                                        const pa = a.price_per_hour ?? Infinity;
                                        const pb = b.price_per_hour ?? Infinity;
                                        return pb - pa;
                                    }
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
                            className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground dark:hover:text-cream/85"
                        >
                            Back
                        </button>
                        <button
                            onClick={() => selectedResource && dispatch({ type: "SET_STEP", payload: 3 })}
                            disabled={!selectedResource}
                            className="px-6 py-2 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Continue
                        </button>
                    </div>
                </div>
            )}

            {/* Step 3: Review */}
            {step === 3 && (
                <div className="max-w-xl mx-auto space-y-6">
                    <div className="p-6 rounded-xl border bg-muted dark:bg-card/50 dark:border-border space-y-4">
                        <div className="space-y-2">
                            <label htmlFor="pool-name" className="text-sm font-medium">Pool Name</label>
                            <input
                                id="pool-name"
                                className="w-full px-3 py-2 border rounded-md bg-card dark:border-border focus:ring-2 focus:ring-ember-500/20 outline-none dark:text-cream"
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

                        {/* AWS provisioning configuration is now in
                            Settings → Providers → AWS (account-wide); NewPool
                            only needs region + GPU + (optional) credential. */}

                        {/* Cluster-specific info */}
                        {isClusterProvider && (
                            <div className="pt-4 border-t border-border/60 dark:border-border/60">
                                <div className="flex items-center gap-2 text-sm text-ember-600 dark:text-ember-400">
                                    <Cloud className="w-4 h-4" />
                                    <span className="font-medium">Cluster-based provisioning</span>
                                </div>
                                <p className="text-xs text-muted-foreground mt-1">
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
                            className="flex-1 px-4 py-2 text-sm font-medium border rounded-md hover:bg-muted dark:hover:bg-card text-fg-secondary dark:text-cream/70 bg-card dark:border-border"
                        >
                            Back
                        </button>
                        <button
                            onClick={handleCreate}
                            disabled={isCreating || !poolName}
                            className="flex-[2] px-6 py-2 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 disabled:opacity-50 flex items-center justify-center gap-2"
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

            {workerResult && (
                <WorkerResultModal
                    result={workerResult}
                    onClose={() => {
                        setWorkerResult(null);
                        navigate("/dashboard/compute/pools");
                    }}
                />
            )}
        </div>
    )
}

function WorkerResultModal({
    result,
    onClose,
}: {
    result: AddWorkerNodeResponse;
    onClose: () => void;
}) {
    const copy = async (text: string, label: string) => {
        try {
            await navigator.clipboard.writeText(text);
            toast.success(`${label} copied`);
        } catch {
            toast.error("Clipboard unavailable; select and copy manually");
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
            <div className="w-full max-w-2xl rounded-xl border bg-background shadow-xl p-6 max-h-[90vh] overflow-y-auto">
                <div className="flex items-start justify-between gap-3 mb-4">
                    <div className="flex items-start gap-3">
                        <div className="mt-0.5 rounded-full bg-emerald-500/10 p-2">
                            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                        </div>
                        <div>
                            <h3 className="text-lg font-semibold">Worker node created</h3>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Paste this <span className="font-mono">.env</span> into the GPU
                                host's <span className="font-mono">inferia-worker</span> deploy and run{" "}
                                <span className="font-mono">docker compose up -d</span>. The node
                                appears in the Compute Nodes list as soon as the worker registers.
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <div className="text-xs text-muted-foreground mb-3">
                    Token expires{" "}
                    <span className="font-mono">
                        {new Date(result.expires_at * 1000).toLocaleString()}
                    </span>.
                </div>

                <div className="mb-4">
                    <div className="flex items-center justify-between mb-1.5">
                        <label className="text-sm font-medium">Worker .env</label>
                        <button
                            onClick={() => copy(result.env_snippet, ".env snippet")}
                            className="text-xs inline-flex items-center gap-1.5 text-ember-600 hover:text-ember-700"
                        >
                            <Copy className="h-3.5 w-3.5" /> Copy
                        </button>
                    </div>
                    <pre className="rounded-md border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                        {result.env_snippet}
                    </pre>
                </div>

                <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400 mb-4">
                    Treat the bootstrap token as a secret. Anyone with the resulting
                    worker JWT can serve inference on behalf of this organisation.
                </div>

                <div className="flex justify-end">
                    <button
                        onClick={onClose}
                        className="px-3 py-1.5 text-sm rounded-md bg-ember-600 hover:bg-ember-700 text-white"
                    >
                        Done
                    </button>
                </div>
            </div>
        </div>
    );
}

function StepProgress({ currentStep }: { currentStep: number }) {
    return (
        <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-border pb-4">
            {[1, 2, 3].map((s, i) => (
                <div key={s} className="flex items-center gap-4">
                    <div className={cn("flex items-center gap-2", currentStep >= s && "text-ember-600 dark:text-ember-400")}>
                        <div className={cn("w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-colors", currentStep >= s ? "bg-ember-600 text-white border-ember-600 dark:border-ember-500 dark:bg-ember-600" : "border-border bg-card")}>{s}</div>
                        {s === 1 ? "Select Provider" : s === 2 ? "Compute Config" : "Review & Create"}
                    </div>
                    {i < 2 && <div className="h-px w-8 bg-muted dark:bg-card" />}
                </div>
            ))}
        </div>
    )
}

function ProviderCard({ provider: p, onSelect }: { provider: any, onSelect: (id: string) => void }) {
    // Always allow click-through to Step 2. Step 2's credential dropdown
    // surfaces existing creds (and shows a banner + a link to the providers
    // settings page if none are configured). This avoids a race where
    // /management/config/providers hasn't returned by the time the user
    // clicks, which used to silently redirect them to the credentials
    // editor even though a credential was set.
    return (
        <button
            disabled={p.disabled}
            onClick={() => !p.disabled && onSelect(p.id)}
            className={cn(
                "text-left group relative p-6 rounded-xl border bg-card dark:border-border hover:border-ember-500/50 dark:hover:border-ember-500/50 transition-colors hover:shadow-md flex flex-col gap-4",
                p.disabled && "opacity-50 cursor-not-allowed hover:border-border dark:hover:border-border hover:shadow-none bg-muted dark:bg-card/50",
                !p.isConfigured && !p.disabled && "border-dashed",
            )}
        >
            <div className={cn("w-12 h-12 rounded-lg flex items-center justify-center transition-colors", p.color, !p.isConfigured && !p.disabled && "opacity-70")}>
                <p.icon className="w-6 h-6" />
            </div>
            <div>
                <h3 className="font-bold text-lg mb-1 group-hover:text-ember-600 dark:group-hover:text-ember-400 transition-colors uppercase tracking-tight text-xs">{p.name}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">{p.description}</p>
                {p.capabilities && (
                    <div className="mt-2 flex flex-wrap gap-1">
                        {p.capabilities.is_ephemeral && <span className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">Ephemeral</span>}
                        {p.capabilities.pricing_model !== 'fixed' && <span className="text-[10px] bg-ember-100 text-ember-700 px-1.5 py-0.5 rounded capitalize">{p.capabilities.pricing_model}</span>}
                    </div>
                )}
                {!p.isConfigured && !p.disabled && p.id !== "worker" && (
                    <div className="mt-2 text-[11px] text-amber-700 dark:text-amber-400">
                        Tip: add a credential under <span className="font-mono">Settings → Providers</span> if you haven't already.
                    </div>
                )}
            </div>
            {p.recommended && <span className="absolute top-4 right-4 text-[10px] font-bold uppercase tracking-wider bg-green-100 text-green-700 px-2 py-1 rounded">Recommended</span>}
        </button>
    )
}

function ResourceFilter({ searchQuery, setSearchQuery, minVram, setMinVram, sortBy, setSortBy }: any) {
    return (
        <div className="flex flex-col md:flex-row gap-3">
            <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                    name="gpuSearch"
                    placeholder="Search GPUs (v100, t4, a100)…"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    autoComplete="off"
                    className="w-full pl-10 pr-4 py-2 bg-card border dark:border-border rounded-lg outline-none focus:ring-2 focus:ring-ember-500/20 transition-colors text-sm"
                />
            </div>
            <select value={minVram} onChange={(e) => setMinVram(Number(e.target.value))} className="px-3 py-2 bg-card border dark:border-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-ember-500/20">
                <option value={0}>All Memory</option>
                <option value={8}>8GB+ VRAM</option>
                <option value={16}>16GB+ VRAM</option>
                <option value={24}>24GB+ VRAM</option>
                <option value={40}>40GB+ VRAM</option>
                <option value={80}>80GB+ VRAM</option>
            </select>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as any)} className="px-3 py-2 bg-card border dark:border-border rounded-lg text-sm outline-none focus:ring-2 focus:ring-ember-500/20">
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
                "w-full cursor-pointer p-4 rounded-xl border bg-card dark:border-border transition-colors relative text-left",
                isSelected ? "border-ember-600 dark:border-ember-500 ring-1 ring-ember-600 dark:ring-ember-500 shadow-sm" : "hover:border-ember-400/30 dark:hover:border-ember-600/30"
            )}
        >
            <div className="flex justify-between items-start mb-2">
                <div className="p-2 bg-muted dark:bg-card rounded-md"><Cpu className="w-5 h-5 text-fg-secondary dark:text-cream/85" /></div>
                <span className="font-bold text-green-600 dark:text-green-400">
                    {res.price_per_hour != null && res.price_per_hour > 0
                        ? `$${res.price_per_hour.toFixed(2)}/hr`
                        : "price N/A"}
                </span>
            </div>
            <h4 className="font-bold">{res.provider_resource_id}</h4>
            <p className="text-sm text-muted-foreground">{res.gpu_type} ({res.gpu_memory_gb}GB VRAM)</p>
            <div className="mt-2 flex gap-2 text-xs text-muted-foreground">
                <span>{res.vcpu} vCPU</span> • <span>{res.ram_gb}GB RAM</span>
            </div>
            {res.pricing_model && res.pricing_model !== 'fixed' && <div className="mt-1"><span className="text-[10px] bg-ember-100 text-ember-700 px-1.5 py-0.5 rounded capitalize">{res.pricing_model}</span></div>}
            {isSelected && <div className="absolute top-4 right-4 w-5 h-5 bg-ember-600 text-white rounded-full flex items-center justify-center"><Check className="w-3 h-3" /></div>}
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
        <div className="pt-4 border-t border-border/60 dark:border-border/60 space-y-3">
            <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Provider</span>
                <span className="font-medium capitalize">{providerName}</span>
            </div>
            
            {isClusterProvider ? (
                <>
                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Region</span>
                        <span className="font-medium">{region || 'N/A'}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">GPU Type</span>
                        <span className="font-medium">{resource?.gpu_type || 'N/A'}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">Instance Type</span>
                        <span className="font-medium capitalize">{useSpot ? 'Spot' : 'On-demand'}</span>
                    </div>
                </>
            ) : (
                <>
                    <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">GPU Type</span>
                        <span className="font-medium">{resource?.gpu_type}</span>
                    </div>
                    {resource?.pricing_model && resource?.pricing_model !== 'fixed' && (
                        <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">Pricing Model</span>
                            <span className="font-medium capitalize">{resource?.pricing_model}</span>
                        </div>
                    )}
                </>
            )}
            
            <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Est. Cost per Hour</span>
                <span className="font-medium">${resource?.price_per_hour || '0.00'}</span>
            </div>
        </div>
    )
}

function CredentialSelection({ provider, credentials, selectedCredential, setSelectedCredential, loading }: any) {
    return (
        <div className="pt-4 border-t border-border/60 dark:border-border/60 space-y-3">
            <div className="space-y-2">
                <label htmlFor="credential-select" className="text-sm font-medium flex items-center gap-2"><Key className="w-4 h-4" /> {provider === "nosana" ? "Nosana API Key" : provider === "akash" ? "Akash Wallet" : "Provider Credential"}</label>
                {loading ? <div className="text-sm text-muted-foreground">Loading credentials...</div> : (
                    <select id="credential-select" value={selectedCredential} onChange={(e) => setSelectedCredential(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-card dark:border-border focus:ring-2 focus:ring-ember-500/20 outline-none dark:text-cream text-sm">
                        <option value="">Select a credential…</option>
                        {credentials.filter((key: any) => key.is_active).map((key: any) => <option key={key.name} value={key.name}>{key.name}</option>)}
                    </select>
                )}
                <p className="text-xs text-muted-foreground">Choose which credential to use for this pool</p>
            </div>
        </div>
    )
}
