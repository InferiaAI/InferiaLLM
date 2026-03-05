import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ConfigService, type ProvidersConfig } from "@/services/configService";
import { Check, ChevronRight, Boxes } from "lucide-react";

type ProviderOption = {
    id: string;
    name: string;
    description: string;
    disabled?: boolean;
};

const PROVIDERS_MAP: Record<string, ProviderOption[]> = {
    cloud: [
        { id: "nosana", name: "Nosana", description: "Decentralized GPU Compute (DePIN)" },
        { id: "akash", name: "Akash Network", description: "Open Cloud Network (DePIN)" },
        { id: "aws", name: "Amazon Web Services", description: "AWS SDK & S3 Integration", disabled: true },
        { id: "gcp", name: "Google Cloud Platform", description: "GCP with SkyPilot orchestration" },
        { id: "azure", name: "Microsoft Azure", description: "Coming Soon", disabled: true },
    ],
    "vector-db": [
        { id: "chroma", name: "ChromaDB", description: "Open-source embedding database" },
    ],
    guardrails: [
        { id: "pii", name: "Built-in PII Redaction", description: "Local sensitive information masking" },
        { id: "groq", name: "Llama Guard via Groq", description: "Fast inference for safety" },
        { id: "lakera", name: "Lakera Guard", description: "Prompt injection protection" },
    ],
};

export default function ProviderList() {
    const { category } = useParams();
    const { data: activeConfig } = useQuery<ProvidersConfig | null>({
        queryKey: ["provider-config"],
        queryFn: async () => {
            try {
                return await ConfigService.getProviderConfig();
            } catch (e) {
                console.error(e);
                return null;
            }
        },
        staleTime: 5 * 60 * 1000,
    });

    const providers = category ? PROVIDERS_MAP[category] || [] : [];
    const categoryTitle = category ? category.charAt(0).toUpperCase() + category.slice(1).replace("-", " ") : "Providers";

    const isConfigured = (providerId: string) => {
        if (!activeConfig) return false;
        switch (providerId) {
            case "aws": return !!activeConfig.cloud?.aws?.access_key_id;
            case "gcp": return !!activeConfig.cloud?.gcp?.project_id || !!activeConfig.cloud?.gcp?.service_account_json;
            case "chroma": return activeConfig.vectordb?.chroma?.is_local !== false ? (!!activeConfig.vectordb?.chroma?.url) : !!activeConfig.vectordb?.chroma?.api_key;
            case "groq": return !!activeConfig.guardrails?.groq?.api_key;
            case "lakera": return !!activeConfig.guardrails?.lakera?.api_key;
            case "nosana": return !!activeConfig.depin?.nosana?.wallet_private_key;
            case "akash": return !!activeConfig.depin?.akash?.mnemonic;
            case "pii": return true; // Always connected/local
            default: return false;
        }
    };

    return (
        <div className="max-w-4xl mx-auto space-y-6">


            <div>
                <h1 className="text-2xl font-bold tracking-tight mb-1">{categoryTitle}</h1>
                <p className="text-muted-foreground">Select a provider to configure.</p>
            </div>

            <div className="grid grid-cols-1 gap-4">
                {providers.map((provider) => {
                    const configured = isConfigured(provider.id);
                    return (
                        <Link
                            key={provider.id}
                            to={`/dashboard/settings/providers/${category}/${provider.id}`}
                            aria-disabled={provider.disabled}
                            onClick={(event) => {
                                if (provider.disabled) {
                                    event.preventDefault();
                                }
                            }}
                            className={`
                flex items-center justify-between p-4 rounded-lg border text-left transition-colors
                ${provider.disabled
                                    ? "opacity-60 cursor-not-allowed bg-muted/50"
                                    : "bg-card hover:bg-accent hover:border-accent-foreground/30 shadow-sm"
                                }
              `}
                        >
                            <div className="flex items-center gap-4">
                                <div className="p-2 bg-primary/10 rounded-md text-primary">
                                    <Boxes className="w-5 h-5" />
                                </div>
                                <div>
                                    <div className="font-semibold flex items-center gap-2">
                                        {provider.name}
                                        {configured && (
                                            <span className="text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full flex items-center gap-1">
                                                <Check className="w-3 h-3" /> Configured
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-sm text-muted-foreground">{provider.description}</p>
                                </div>
                            </div>
                            {!provider.disabled && <ChevronRight className="w-5 h-5 text-muted-foreground" aria-hidden="true" />}
                        </Link>
                    );
                })}

                {providers.length === 0 && (
                    <div className="p-12 text-center text-muted-foreground border rounded-lg border-dashed">
                        Category not found or empty.
                    </div>
                )}
            </div>
        </div>
    );
}
