import { useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { ConfigService, type ProvidersConfig, initialProviderConfig } from "@/services/configService";
import { ChevronRight, Save, Loader2, Edit2, X, CheckCircle, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

export default function ProviderConfigPage() {
    const { category, providerId } = useParams();
    const navigate = useNavigate();
    const [config, setConfig] = useState<ProvidersConfig>(initialProviderConfig);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [isConfigured, setIsConfigured] = useState(false);

    useEffect(() => {
        loadConfig();
    }, []);

    const loadConfig = async () => {
        try {
            const data = await ConfigService.getProviderConfig();
            // Merge with initial to ensure structure exists
            const merged = {
                cloud: { aws: { ...initialProviderConfig.cloud.aws, ...data.cloud?.aws } },
                vectordb: { chroma: { ...initialProviderConfig.vectordb.chroma, ...data.vectordb?.chroma } },
                guardrails: {
                    groq: { ...initialProviderConfig.guardrails.groq, ...data.guardrails?.groq },
                    lakera: { ...initialProviderConfig.guardrails.lakera, ...data.guardrails?.lakera }
                },
                depin: {
                    nosana: { ...initialProviderConfig.depin.nosana, ...data.depin?.nosana },
                    akash: { ...initialProviderConfig.depin.akash, ...data.depin?.akash }
                }
            };
            setConfig(merged);

            if (checkConfigured(merged, providerId)) {
                setIsConfigured(true);
                setIsEditing(false);
            } else {
                setIsConfigured(false);
                setIsEditing(true);
            }
        } catch (e) {
            toast.error("Failed to load configuration");
            // Fallback to initial
            setConfig(initialProviderConfig);
            setIsEditing(true);
        } finally {
            setLoading(false);
        }
    };

    const checkConfigured = (data: ProvidersConfig, pid?: string) => {
        if (!pid) return false;
        switch (pid) {
            case "aws": return !!data.cloud.aws.access_key_id;
            case "chroma": return data.vectordb.chroma.is_local !== false ? (!!data.vectordb.chroma.url) : !!data.vectordb.chroma.api_key;
            case "groq": return !!data.guardrails.groq.api_key;
            case "lakera": return !!data.guardrails.lakera.api_key;
            case "nosana": return !!data.depin.nosana.wallet_private_key;
            case "akash": return !!data.depin.akash.mnemonic;
            default: return false;
        }
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        setSaving(true);
        try {
            await ConfigService.updateProviderConfig(config);
            toast.success("Configuration saved successfully");
            setIsConfigured(true);
            setIsEditing(false);
        } catch (e) {
            toast.error("Failed to save configuration");
        } finally {
            setSaving(false);
        }
    };

    // Helper to update deeply nested state
    const updateField = (path: string[], value: any) => {
        setConfig(prev => {
            const newState = { ...prev };
            let current: any = newState;
            for (let i = 0; i < path.length - 1; i++) {
                current[path[i]] = { ...current[path[i]] };
                current = current[path[i]];
            }
            current[path[path.length - 1]] = value;
            return newState;
        });
    };

    // Render Logic based on providerId
    const renderFormFields = () => {
        switch (providerId) {
            case "aws":
                return (
                    <>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Access Key ID</label>
                            <input
                                value={config.cloud.aws.access_key_id || ""}
                                onChange={(e) => updateField(['cloud', 'aws', 'access_key_id'], e.target.value)}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                placeholder="AKIA..."
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Secret Access Key</label>
                            <input
                                type="password"
                                value={config.cloud.aws.secret_access_key || ""}
                                onChange={(e) => updateField(['cloud', 'aws', 'secret_access_key'], e.target.value)}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                placeholder="********"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Region</label>
                            <input
                                value={config.cloud.aws.region || "ap-south-1"}
                                onChange={(e) => updateField(['cloud', 'aws', 'region'], e.target.value)}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            />
                        </div>
                    </>
                );
            case "chroma":
                return (
                    <div className="space-y-4">
                        <div className="flex items-center gap-4 p-4 border rounded-lg bg-muted/30">
                            <div className="flex-1">
                                <label className="text-sm font-medium">Connection Mode</label>
                                <p className="text-xs text-muted-foreground">Choose between self-hosted or cloud-managed Chroma.</p>
                            </div>
                            <div className="flex bg-muted rounded-lg p-1">
                                <button
                                    type="button"
                                    onClick={() => updateField(['vectordb', 'chroma', 'is_local'], true)}
                                    className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${config.vectordb.chroma.is_local !== false ? "bg-background shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                                >
                                    Local
                                </button>
                                <button
                                    type="button"
                                    onClick={() => updateField(['vectordb', 'chroma', 'is_local'], false)}
                                    className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${config.vectordb.chroma.is_local === false ? "bg-background shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                                >
                                    Cloud
                                </button>
                            </div>
                        </div>

                        {config.vectordb.chroma.is_local !== false ? (
                            <div className="space-y-2 animate-in fade-in zoom-in-95 duration-200">
                                <label className="text-sm font-medium">Chroma URL</label>
                                <input
                                    value={config.vectordb.chroma.url || "http://localhost:8000"}
                                    onChange={(e) => updateField(['vectordb', 'chroma', 'url'], e.target.value)}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    placeholder="http://localhost:8000"
                                />
                                <p className="text-xs text-muted-foreground">Default local URL is http://localhost:8000</p>
                            </div>
                        ) : (
                            <div className="space-y-4 animate-in fade-in zoom-in-95 duration-200">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Chroma API Key</label>
                                    <input
                                        type="password"
                                        value={config.vectordb.chroma.api_key || ""}
                                        onChange={(e) => updateField(['vectordb', 'chroma', 'api_key'], e.target.value)}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                        placeholder="ck-..."
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Tenant ID</label>
                                    <input
                                        value={config.vectordb.chroma.tenant || ""}
                                        onChange={(e) => updateField(['vectordb', 'chroma', 'tenant'], e.target.value)}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    />
                                </div>
                            </div>
                        )}
                        
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Database Name</label>
                            <input
                                value={config.vectordb.chroma.database || ""}
                                onChange={(e) => updateField(['vectordb', 'chroma', 'database'], e.target.value)}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                placeholder="default_database"
                            />
                            <p className="text-xs text-muted-foreground">Required for organization isolation.</p>
                        </div>
                    </div>
                );
            case "groq":
                return (
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Groq API Key</label>
                        <input
                            type="password"
                            value={config.guardrails.groq.api_key || ""}
                            onChange={(e) => updateField(['guardrails', 'groq', 'api_key'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            placeholder="gsk_..."
                        />
                    </div>
                );
            case "lakera":
                return (
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Lakera Guard API Key</label>
                        <input
                            type="password"
                            value={config.guardrails.lakera.api_key || ""}
                            onChange={(e) => updateField(['guardrails', 'lakera', 'api_key'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        />
                    </div>
                );
            case "nosana":
                return (
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Wallet Private Key</label>
                        <input
                            type="password"
                            value={config.depin.nosana.wallet_private_key || ""}
                            onChange={(e) => updateField(['depin', 'nosana', 'wallet_private_key'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            placeholder="Base58..."
                        />
                    </div>
                )
            case "akash":
                return (
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Mnemonic</label>
                        <input
                            type="password"
                            value={config.depin.akash.mnemonic || ""}
                            onChange={(e) => updateField(['depin', 'akash', 'mnemonic'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        />
                    </div>
                )
            case "pii":
                return (
                    <div className="p-4 bg-muted/30 border rounded-lg space-y-2">
                        <div className="font-medium flex items-center gap-2">
                            <ShieldCheck className="w-4 h-4 text-green-600" />
                            Local Service Active
                        </div>
                        <p className="text-sm text-muted-foreground">
                            PII Redaction is a built-in local service using LLM-Guard. It does not require external API keys and is always available for use in your deployments.
                        </p>
                    </div>
                );
            default:
                return <div>Unknown Provider</div>;
        }
    };

    const providerName = providerId ? providerId.charAt(0).toUpperCase() + providerId.slice(1) : "Unknown";
    const categoryTitle = category ? category.charAt(0).toUpperCase() + category.slice(1).replace("-", " ") : "Providers";

    if (loading) return <div className="p-12 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="max-w-3xl mx-auto space-y-6">


            <div className="bg-card border rounded-xl overflow-hidden shadow-sm">
                <div className="p-6 border-b flex justify-between items-center">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Configure {providerName}</h1>
                        <p className="text-muted-foreground mt-1">
                            {isConfigured ? "Credentials securely stored." : "Enter credentials securely. Stored locally."}
                        </p>
                    </div>
                    {isConfigured && !isEditing && (
                        <div className="flex items-center gap-2 text-green-600 bg-green-50 px-3 py-1 rounded-full text-sm font-medium">
                            <ShieldCheck className="w-4 h-4" /> Configured
                        </div>
                    )}
                </div>

                {!isEditing && isConfigured ? (
                    <div className="p-8 flex flex-col items-center justify-center text-center space-y-4">
                        <div className="bg-green-100 p-4 rounded-full text-green-600">
                            <CheckCircle className="w-12 h-12" />
                        </div>
                        <div>
                            <h3 className="text-lg font-medium">Configuration Active</h3>
                            <p className="text-muted-foreground max-w-sm mt-1">
                                Your credentials for {providerName} are set and encrypted.
                            </p>
                        </div>
                        <button
                            onClick={() => setIsEditing(true)}
                            className="mt-4 flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md hover:bg-primary/90 transition-colors"
                        >
                            <Edit2 className="w-4 h-4" /> Edit Configuration
                        </button>
                    </div>
                ) : (
                    <form onSubmit={handleSave} className="p-6 space-y-6 animate-in fade-in slide-in-from-top-2 duration-300">
                        {renderFormFields()}

                        <div className="pt-4 flex justify-end gap-3">
                            {isConfigured && (
                                <button
                                    type="button"
                                    onClick={() => setIsEditing(false)}
                                    disabled={saving}
                                    className="flex items-center gap-2 px-4 py-2 rounded-md border hover:bg-accent transition-colors"
                                >
                                    <X className="w-4 h-4" /> Cancel
                                </button>
                            )}
                            <button
                                type="submit"
                                disabled={saving}
                                className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 gap-2"
                            >
                                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                                {saving ? "Saving..." : "Save Changes"}
                            </button>
                        </div>
                    </form>
                )}
            </div>
        </div>
    );
}
