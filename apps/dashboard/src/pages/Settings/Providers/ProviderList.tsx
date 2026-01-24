import { useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { ConfigService, type ProvidersConfig } from "@/services/configService";
import { Check, ChevronRight, Boxes } from "lucide-react";

const PROVIDERS_MAP: Record<string, any[]> = {
    cloud: [
        { id: "aws", name: "Amazon Web Services", description: "AWS SDK & S3 Integration" },
        { id: "gcp", name: "Google Cloud Platform", description: "Coming Soon", disabled: true },
        { id: "azure", name: "Microsoft Azure", description: "Coming Soon", disabled: true },
        { id: "nosana", name: "Nosana", description: "Decentralized GPU Compute (DePIN)" },
        { id: "akash", name: "Akash Network", description: "Open Cloud Network (DePIN)" },
    ],
    "vector-db": [
        { id: "chroma", name: "ChromaDB", description: "Open-source embedding database" },
        { id: "pinecone", name: "Pinecone", description: "Coming Soon", disabled: true },
    ],
    guardrails: [
        { id: "pii", name: "Built-in PII Redaction", description: "Local sensitive information masking" },
        { id: "groq", name: "Llama Guard via Groq", description: "Fast inference for safety" },
        { id: "lakera", name: "Lakera Guard", description: "Prompt injection protection" },
    ],
};

export default function ProviderList() {
    const { category } = useParams();
    const navigate = useNavigate();
    const [activeConfig, setActiveConfig] = useState<ProvidersConfig | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadConfig();
    }, []);

    const loadConfig = async () => {
        try {
            const data = await ConfigService.getProviderConfig();
            setActiveConfig(data);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const providers = category ? PROVIDERS_MAP[category] || [] : [];
    const categoryTitle = category ? category.charAt(0).toUpperCase() + category.slice(1).replace("-", " ") : "Providers";

    const isConfigured = (providerId: string) => {
        if (!activeConfig) return false;
        switch (providerId) {
            case "aws": return !!activeConfig.cloud?.aws?.access_key_id;
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
                        <button
                            key={provider.id}
                            disabled={provider.disabled}
                            onClick={() => navigate(`/dashboard/settings/providers/${category}/${provider.id}`)}
                            className={`
                flex items-center justify-between p-4 rounded-lg border text-left transition-all
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
                            {!provider.disabled && <ChevronRight className="w-5 h-5 text-muted-foreground" />}
                        </button>
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
