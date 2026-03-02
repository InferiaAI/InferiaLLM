import { useNavigate, useParams } from "react-router-dom";
import { useEffect, useState, useReducer } from "react";
import { ConfigService, type ProvidersConfig, type NosanaApiKeyResponse, initialProviderConfig } from "@/services/configService";
import { ChevronRight, Save, Loader2, Edit2, X, CheckCircle, ShieldCheck, Plus, Trash2, Key } from "lucide-react";
import { toast } from "sonner";

type State = {
    config: ProvidersConfig;
    loading: boolean;
    saving: boolean;
    isEditing: boolean;
    isConfigured: boolean;
    nosanaApiKeys: NosanaApiKeyResponse[];
    showAddKeyModal: boolean;
    newKeyName: string;
    newKeyValue: string;
    loadingKeys: boolean;
    showDeleteModal: boolean;
    keyToDelete: string | null;
};

type Action =
    | { type: 'SET_FIELD'; field: keyof State; value: any }
    | { type: 'UPDATE_CONFIG'; path: string[]; value: any }
    | { type: 'RESET_ADD_KEY_MODAL' };

function reducer(state: State, action: Action): State {
    switch (action.type) {
        case 'SET_FIELD':
            return { ...state, [action.field]: action.value };
        case 'UPDATE_CONFIG': {
            const newState = { ...state.config };
            let current: any = newState;
            for (let i = 0; i < action.path.length - 1; i++) {
                current[action.path[i]] = { ...current[action.path[i]] };
                current = current[action.path[i]];
            }
            current[action.path[action.path.length - 1]] = action.value;
            return { ...state, config: newState };
        }
        case 'RESET_ADD_KEY_MODAL':
            return { ...state, showAddKeyModal: false, newKeyName: "", newKeyValue: "" };
        case 'SHOW_DELETE_MODAL':
            return { ...state, showDeleteModal: true, keyToDelete: action.value };
        case 'HIDE_DELETE_MODAL':
            return { ...state, showDeleteModal: false, keyToDelete: null };
        default:
            return state;
    }
}

const initialState: State = {
    config: initialProviderConfig,
    loading: true,
    saving: false,
    isEditing: false,
    isConfigured: false,
    nosanaApiKeys: [],
    showAddKeyModal: false,
    newKeyName: "",
    newKeyValue: "",
    loadingKeys: false,
    showDeleteModal: false,
    keyToDelete: null,
};

export default function ProviderConfigPage() {
    const { category, providerId } = useParams();
    const navigate = useNavigate();
    const [state, dispatch] = useReducer(reducer, initialState);
    const {
        config, loading, saving, isEditing, isConfigured,
        nosanaApiKeys, showAddKeyModal, newKeyName, newKeyValue, loadingKeys,
        showDeleteModal, keyToDelete
    } = state;

    useEffect(() => {
        loadConfig();
        if (providerId === "nosana") {
            loadNosanaApiKeys();
        }
    }, [providerId]);

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
            dispatch({ type: 'SET_FIELD', field: 'config', value: merged });

            if (checkConfigured(merged, providerId)) {
                dispatch({ type: 'SET_FIELD', field: 'isConfigured', value: true });
                dispatch({ type: 'SET_FIELD', field: 'isEditing', value: false });
            } else {
                dispatch({ type: 'SET_FIELD', field: 'isConfigured', value: false });
                dispatch({ type: 'SET_FIELD', field: 'isEditing', value: true });
            }
        } catch (e) {
            toast.error("Failed to load configuration");
            dispatch({ type: 'SET_FIELD', field: 'config', value: initialProviderConfig });
            dispatch({ type: 'SET_FIELD', field: 'isEditing', value: true });
        } finally {
            dispatch({ type: 'SET_FIELD', field: 'loading', value: false });
        }
    };

    const loadNosanaApiKeys = async () => {
        try {
            dispatch({ type: 'SET_FIELD', field: 'loadingKeys', value: true });
            const keys = await ConfigService.listNosanaApiKeys();
            dispatch({ type: 'SET_FIELD', field: 'nosanaApiKeys', value: keys });

            // Sync with main config to prevent stale data when saving main config
            // Note: nosanaApiKeys doesn't have the full secret 'key', but the backend
            // merge logic now handles masked values by preserving existing unmasked ones.
            const apiKeyEntries = keys.map(k => ({
                name: k.name,
                key: "********", // Placeholder to let backend know we have this key
                is_active: k.is_active
            }));
            dispatch({ type: 'UPDATE_CONFIG', path: ['depin', 'nosana', 'api_keys'], value: apiKeyEntries });
        } catch (e) {
            toast.error("Failed to load Nosana API keys");
        } finally {
            dispatch({ type: 'SET_FIELD', field: 'loadingKeys', value: false });
        }
    };

    const handleAddApiKey = async () => {
        if (!newKeyName.trim() || !newKeyValue.trim()) {
            toast.error("Please provide both name and API key");
            return;
        }
        try {
            await ConfigService.addNosanaApiKey(newKeyName.trim(), newKeyValue.trim());
            toast.success(`API key "${newKeyName}" added successfully`);
            dispatch({ type: 'RESET_ADD_KEY_MODAL' });
            loadNosanaApiKeys();
        } catch (e) {
            toast.error("Failed to add API key");
        }
    };

    const handleDeleteApiKey = async (name: string) => {
        dispatch({ type: 'SHOW_DELETE_MODAL', value: name });
    };

    const confirmDeleteApiKey = async () => {
        if (!keyToDelete) return;
        const name = keyToDelete;
        try {
            await ConfigService.deleteNosanaApiKey(name);
            toast.success(`API key "${name}" deleted successfully`);
            dispatch({ type: 'HIDE_DELETE_MODAL' });
            loadNosanaApiKeys();
        } catch (e) {
            toast.error("Failed to delete API key");
        }
    };

    const checkConfigured = (data: ProvidersConfig, pid?: string) => {
        if (!pid) return false;
        switch (pid) {
            case "aws": return !!data.cloud.aws.access_key_id;
            case "chroma": return data.vectordb.chroma.is_local !== false ? (!!data.vectordb.chroma.url) : !!data.vectordb.chroma.api_key;
            case "groq": return !!data.guardrails.groq.api_key;
            case "lakera": return !!data.guardrails.lakera.api_key;
            case "nosana": return !!data.depin.nosana.wallet_private_key || !!data.depin.nosana.api_key || (nosanaApiKeys && nosanaApiKeys.length > 0);
            case "akash": return !!data.depin.akash.mnemonic;
            default: return false;
        }
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        dispatch({ type: 'SET_FIELD', field: 'saving', value: true });
        try {
            await ConfigService.updateProviderConfig(config);
            toast.success("Configuration saved successfully");
            dispatch({ type: 'SET_FIELD', field: 'isConfigured', value: true });
            dispatch({ type: 'SET_FIELD', field: 'isEditing', value: false });
        } catch (e) {
            toast.error("Failed to save configuration");
        } finally {
            dispatch({ type: 'SET_FIELD', field: 'saving', value: false });
        }
    };

    const updateField = (path: string[], value: any) => {
        dispatch({ type: 'UPDATE_CONFIG', path, value });
    };

    const providerName = providerId ? providerId.charAt(0).toUpperCase() + providerId.slice(1) : "Unknown";
    const categoryTitle = category ? category.charAt(0).toUpperCase() + category.slice(1).replace("-", " ") : "Providers";

    if (loading) return <div className="p-12 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="max-w-3xl mx-auto space-y-6">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
                <span>Settings</span>
                <ChevronRight className="w-3 h-3" />
                <span>Providers</span>
                <ChevronRight className="w-3 h-3" />
                <span className="text-foreground font-medium">{categoryTitle}</span>
                <ChevronRight className="w-3 h-3" />
                <span className="text-foreground font-medium">{providerName}</span>
            </div>

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
                            type="button"
                            onClick={() => dispatch({ type: 'SET_FIELD', field: 'isEditing', value: true })}
                            className="mt-4 flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md hover:bg-primary/90 transition-colors"
                        >
                            <Edit2 className="w-4 h-4" /> Edit Configuration
                        </button>
                    </div>
                ) : (
                    <form onSubmit={handleSave} className="p-6 space-y-6 animate-in fade-in slide-in-from-top-2 duration-300">
                        <ProviderFormFields
                            providerId={providerId}
                            config={config}
                            updateField={updateField}
                            nosanaApiKeys={nosanaApiKeys}
                            loadingKeys={loadingKeys}
                            handleAddKey={() => dispatch({ type: 'SET_FIELD', field: 'showAddKeyModal', value: true })}
                            handleDeleteApiKey={handleDeleteApiKey}
                        />

                        <div className="pt-4 flex justify-end gap-3">
                            {isConfigured && (
                                <button
                                    type="button"
                                    onClick={() => dispatch({ type: 'SET_FIELD', field: 'isEditing', value: false })}
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

            {showAddKeyModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-card border rounded-xl p-6 w-full max-w-md mx-4 space-y-4">
                        <h3 className="text-lg font-semibold">Add Nosana API Key</h3>
                        <div className="space-y-3">
                            <div className="space-y-2">
                                <label htmlFor="key-name" className="text-sm font-medium">Key Name</label>
                                <input
                                    id="key-name"
                                    value={newKeyName}
                                    onChange={(e) => dispatch({ type: 'SET_FIELD', field: 'newKeyName', value: e.target.value })}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    placeholder="e.g., Piyush, Jesse, Production..."
                                />
                                <p className="text-xs text-muted-foreground">
                                    A friendly name to identify this key
                                </p>
                            </div>
                            <div className="space-y-2">
                                <label htmlFor="key-value" className="text-sm font-medium">API Key</label>
                                <input
                                    id="key-value"
                                    type="password"
                                    value={newKeyValue}
                                    onChange={(e) => dispatch({ type: 'SET_FIELD', field: 'newKeyValue', value: e.target.value })}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    placeholder="nos_..."
                                />
                            </div>
                        </div>
                        <div className="flex justify-end gap-3 pt-2">
                            <button
                                type="button"
                                onClick={() => dispatch({ type: 'RESET_ADD_KEY_MODAL' })}
                                className="px-4 py-2 text-sm font-medium rounded-md border hover:bg-accent transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={handleAddApiKey}
                                className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                            >
                                Add Key
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {showDeleteModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[60]">
                    <div className="bg-card border rounded-xl p-6 w-full max-w-md mx-4 space-y-4 shadow-xl border-red-100">
                        <div className="flex items-center gap-3 text-red-600">
                            <div className="p-2 bg-red-50 rounded-full">
                                <Trash2 className="w-6 h-6" />
                            </div>
                            <h3 className="text-lg font-semibold">Delete API Key?</h3>
                        </div>

                        <div className="space-y-3">
                            <p className="text-sm text-foreground">
                                Are you sure you want to delete the API key <span className="font-bold">&quot;{keyToDelete}&quot;</span>?
                            </p>

                            <div className="p-4 bg-red-50 border border-red-100 rounded-lg space-y-2">
                                <p className="text-xs font-bold text-red-800 flex items-center gap-1.5">
                                    <ShieldCheck className="w-3.5 h-3.5" />
                                    WARNING: RECURSIVE DELETION
                                </p>
                                <p className="text-xs text-red-700 leading-relaxed">
                                    Deleting this key will automatically <span className="font-bold underline">terminate all active deployments</span> and <span className="font-bold underline">delete all compute pools</span> associated with it. This action cannot be undone.
                                </p>
                            </div>
                        </div>

                        <div className="flex justify-end gap-3 pt-2">
                            <button
                                type="button"
                                onClick={() => dispatch({ type: 'HIDE_DELETE_MODAL' })}
                                className="px-4 py-2 text-sm font-medium rounded-md border hover:bg-accent transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={confirmDeleteApiKey}
                                className="px-4 py-2 text-sm font-medium bg-red-600 text-white rounded-md hover:bg-red-700 transition-colors shadow-sm"
                            >
                                Delete Key & Resources
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function AWSFields({ config, updateField }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void }) {
    return (
        <>
            <div className="space-y-2">
                <label htmlFor="aws-access-key" className="text-sm font-medium">Access Key ID</label>
                <input
                    id="aws-access-key"
                    value={config.cloud.aws.access_key_id || ""}
                    onChange={(e) => updateField(['cloud', 'aws', 'access_key_id'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="AKIA..."
                />
            </div>
            <div className="space-y-2">
                <label htmlFor="aws-secret-key" className="text-sm font-medium">Secret Access Key</label>
                <input
                    id="aws-secret-key"
                    type="password"
                    value={config.cloud.aws.secret_access_key || ""}
                    onChange={(e) => updateField(['cloud', 'aws', 'secret_access_key'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="********"
                />
            </div>
            <div className="space-y-2">
                <label htmlFor="aws-region" className="text-sm font-medium">Region</label>
                <input
                    id="aws-region"
                    value={config.cloud.aws.region || "ap-south-1"}
                    onChange={(e) => updateField(['cloud', 'aws', 'region'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
            </div>
        </>
    );
}

function ChromaFields({ config, updateField }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void }) {
    return (
        <div className="space-y-4">
            <div className="flex items-center gap-4 p-4 border rounded-lg bg-muted/30">
                <div className="flex-1">
                    <span className="text-sm font-medium">Connection Mode</span>
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
                    <label htmlFor="chroma-url" className="text-sm font-medium">Chroma URL</label>
                    <input
                        id="chroma-url"
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
                        <label htmlFor="chroma-api-key" className="text-sm font-medium">Chroma API Key</label>
                        <input
                            id="chroma-api-key"
                            type="password"
                            value={config.vectordb.chroma.api_key || ""}
                            onChange={(e) => updateField(['vectordb', 'chroma', 'api_key'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                            placeholder="ck-..."
                        />
                    </div>
                    <div className="space-y-2">
                        <label htmlFor="chroma-tenant" className="text-sm font-medium">Tenant ID</label>
                        <input
                            id="chroma-tenant"
                            value={config.vectordb.chroma.tenant || ""}
                            onChange={(e) => updateField(['vectordb', 'chroma', 'tenant'], e.target.value)}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        />
                    </div>
                </div>
            )}

            <div className="space-y-2">
                <label htmlFor="chroma-db" className="text-sm font-medium">Database Name</label>
                <input
                    id="chroma-db"
                    value={config.vectordb.chroma.database || ""}
                    onChange={(e) => updateField(['vectordb', 'chroma', 'database'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="default_database"
                />
                <p className="text-xs text-muted-foreground">Required for organization isolation.</p>
            </div>
        </div>
    );
}

function GroqFields({ config, updateField }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void }) {
    return (
        <div className="space-y-2">
            <label htmlFor="groq-api-key" className="text-sm font-medium">Groq API Key</label>
            <input
                id="groq-api-key"
                type="password"
                value={config.guardrails.groq.api_key || ""}
                onChange={(e) => updateField(['guardrails', 'groq', 'api_key'], e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                placeholder="gsk_..."
            />
        </div>
    );
}

function LakeraFields({ config, updateField }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void }) {
    return (
        <div className="space-y-2">
            <label htmlFor="lakera-api-key" className="text-sm font-medium">Lakera Guard API Key</label>
            <input
                id="lakera-api-key"
                type="password"
                value={config.guardrails.lakera.api_key || ""}
                onChange={(e) => updateField(['guardrails', 'lakera', 'api_key'], e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
        </div>
    );
}

function NosanaFields({
    config,
    updateField,
    loadingKeys,
    nosanaApiKeys,
    handleAddKey,
    handleDeleteApiKey
}: {
    config: ProvidersConfig;
    updateField: (path: string[], value: any) => void;
    loadingKeys: boolean;
    nosanaApiKeys: NosanaApiKeyResponse[];
    handleAddKey: () => void;
    handleDeleteApiKey: (name: string) => void;
}) {
    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h3 className="text-sm font-semibold flex items-center gap-2">
                    <Key className="w-4 h-4" />
                    Wallet Configuration
                </h3>
                <div className="p-3 bg-emerald-50 border border-emerald-100 rounded-lg text-xs text-emerald-700">
                    Nosana supports both Wallet-based (on-chain) and API-based (credit) deployments. Enter a Private Key for on-chain deployments.
                </div>
                <div className="space-y-2">
                    <label htmlFor="nosana-wallet" className="text-sm font-medium">Wallet Private Key</label>
                    <input
                        id="nosana-wallet"
                        type="password"
                        value={config.depin.nosana.wallet_private_key || ""}
                        onChange={(e) => updateField(['depin', 'nosana', 'wallet_private_key'], e.target.value)}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        placeholder="Base58 Private Key..."
                    />
                    <p className="text-[10px] text-muted-foreground italic">Use for direct on-chain deployments via Solana.</p>
                </div>
            </div>

            <div className="relative py-2 text-center">
                <div className="absolute inset-0 flex items-center" aria-hidden="true">
                    <div className="w-full border-t border-muted"></div>
                </div>
                <span className="relative bg-background px-2 text-xs text-muted-foreground uppercase">OR</span>
            </div>

            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Key className="w-4 h-4" />
                        API Keys for Credit-Based Deployments
                    </h3>
                    <button
                        type="button"
                        onClick={handleAddKey}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                    >
                        <Plus className="w-3.5 h-3.5" />
                        Add Key
                    </button>
                </div>

                <p className="text-xs text-muted-foreground">
                    Add multiple Nosana API keys with friendly names.
                    These will be available as options when creating compute pools.
                </p>

                {loadingKeys ? (
                    <div className="text-center py-4 text-muted-foreground text-sm">
                        Loading API keys...
                    </div>
                ) : nosanaApiKeys.length === 0 ? (
                    <div className="text-center py-6 bg-muted/30 rounded-lg border border-dashed">
                        <Key className="w-8 h-8 mx-auto text-muted-foreground mb-2" />
                        <p className="text-sm text-muted-foreground">No API keys configured</p>
                        <p className="text-xs text-muted-foreground mt-1">Add your first key to get started</p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {nosanaApiKeys.map((key) => (
                            <div
                                key={key.name}
                                className="flex items-center justify-between p-3 bg-muted/30 rounded-lg border"
                            >
                                <div className="flex items-center gap-3">
                                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                        <Key className="w-4 h-4 text-primary" />
                                    </div>
                                    <div>
                                        <p className="font-medium text-sm">{key.name}</p>
                                        <p className="text-xs text-muted-foreground">
                                            {key.is_active ? 'Active' : 'Inactive'}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => handleDeleteApiKey(key.name)}
                                    className="p-2 text-muted-foreground hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
                                    title="Delete API key"
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                {config.depin.nosana.api_key && (
                    <div className="mt-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                        <p className="text-xs text-amber-800 font-medium mb-2">Legacy API Key Detected</p>
                        <div className="space-y-2">
                            <label htmlFor="nosana-legacy-key" className="text-xs font-medium text-amber-800">Existing API Key</label>
                            <input
                                id="nosana-legacy-key"
                                type="password"
                                value={config.depin.nosana.api_key || ""}
                                onChange={(e) => updateField(['depin', 'nosana', 'api_key'], e.target.value)}
                                className="flex h-9 w-full rounded-md border border-amber-200 bg-white px-3 py-2 text-sm"
                                placeholder="nos_..."
                            />
                            <p className="text-[10px] text-amber-700">
                                This legacy key will be available as &quot;default&quot; in pool creation.
                                Consider migrating to the named keys above.
                            </p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function AkashFields({ config, updateField, handleAddKey }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void; handleAddKey: () => void }) {
    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h3 className="text-sm font-semibold flex items-center gap-2">
                    <Key className="w-4 h-4" />
                    Legacy Mnemonic
                </h3>
                <div className="p-3 bg-emerald-50 border border-emerald-100 rounded-lg text-xs text-emerald-700">
                    Akash uses a mnemonic phrase for wallet authentication. You can use the legacy field below or set up multiple wallets using the credential management system.
                </div>
                <div className="space-y-2">
                    <label htmlFor="akash-mnemonic" className="text-sm font-medium">Mnemonic Phrase</label>
                    <input
                        id="akash-mnemonic"
                        type="password"
                        value={config.depin.akash.mnemonic || ""}
                        onChange={(e) => updateField(['depin', 'akash', 'mnemonic'], e.target.value)}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        placeholder="word1 word2 word3..."
                    />
                    <p className="text-[10px] text-muted-foreground italic">Your Akash wallet mnemonic for on-chain deployments.</p>
                </div>
            </div>

            <div className="relative py-2 text-center">
                <div className="absolute inset-0 flex items-center" aria-hidden="true">
                    <div className="w-full border-t border-muted"></div>
                </div>
                <span className="relative bg-background px-2 text-xs text-muted-foreground uppercase">OR</span>
            </div>

            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Key className="w-4 h-4" />
                        Managed Wallets (Universal Credential System)
                    </h3>
                    <button
                        type="button"
                        onClick={handleAddKey}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                    >
                        <Plus className="w-3.5 h-3.5" />
                        Add Wallet
                    </button>
                </div>

                <p className="text-xs text-muted-foreground">
                    Add multiple Akash wallets with friendly names using the universal credential system.
                    These work exactly like Nosana API keys but for Akash mnemonics.
                </p>

                <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                    <p className="text-xs text-green-800 font-medium mb-1">Universal System Ready!</p>
                    <p className="text-xs text-green-700">
                        To fully enable this UI, update the component state to track provider type
                        and use the universal <code>ConfigService.listProviderCredentials('akash')</code> API.
                    </p>
                </div>
            </div>
        </div>
    );
}

function PIIFields() {
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
}

function ProviderFormFields({
    providerId,
    config,
    updateField,
    nosanaApiKeys,
    loadingKeys,
    handleAddKey,
    handleDeleteApiKey,
}: {
    providerId?: string;
    config: ProvidersConfig;
    updateField: (path: string[], value: any) => void;
    nosanaApiKeys: NosanaApiKeyResponse[];
    loadingKeys: boolean;
    handleAddKey: () => void;
    handleDeleteApiKey: (name: string) => void;
}) {
    switch (providerId) {
        case "aws":
            return <AWSFields config={config} updateField={updateField} />;
        case "chroma":
            return <ChromaFields config={config} updateField={updateField} />;
        case "groq":
            return <GroqFields config={config} updateField={updateField} />;
        case "lakera":
            return <LakeraFields config={config} updateField={updateField} />;
        case "nosana":
            return (
                <NosanaFields
                    config={config}
                    updateField={updateField}
                    loadingKeys={loadingKeys}
                    nosanaApiKeys={nosanaApiKeys}
                    handleAddKey={handleAddKey}
                    handleDeleteApiKey={handleDeleteApiKey}
                />
            );
        case "akash":
            return <AkashFields config={config} updateField={updateField} handleAddKey={handleAddKey} />;
        case "pii":
            return <PIIFields />;
        default:
            return <div>Unknown Provider</div>;
    }
}
