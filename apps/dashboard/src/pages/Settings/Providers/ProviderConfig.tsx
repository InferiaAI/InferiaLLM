import { useNavigate, useParams } from "react-router-dom";
import { useEffect, useReducer } from "react";
import { ConfigService, type ProvidersConfig, type NosanaApiKeyResponse, type HfTokenResponse, initialProviderConfig } from "@/services/configService";
import { ChevronRight, Save, Loader2, Edit2, X, CheckCircle, ShieldCheck, Plus, Trash2, Key, HelpCircle } from "lucide-react";
import { toast } from "sonner";
import { EngineAmiSection } from "./EngineAmiSection";

// Small inline "where to find" hint rendered below a credential field
// label. The HelpCircle icon plus a one-line writeup tells the user
// exactly which screen of the cloud provider's console holds the value.
// No external link — keep the user in the dashboard.
function CredHint({ children }: { children: React.ReactNode }) {
    return (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1">
            <HelpCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-blue-500" />
            <span>{children}</span>
        </p>
    );
}

// True when value looks like one of the backend's mask outputs:
//   - "********" (full-mask)
//   - "XXXX...XXXX" — 4 chars + literal "..." + 4 chars (partial mask)
// Matches the contract enforced by api_gateway/management/configuration.py.
function isMaskedSecret(value: string | null | undefined): boolean {
    if (!value) return false;
    if (value === "********") return true;
    if (value.length === 11 && value.substring(4, 7) === "..." && !value.includes("*")) return true;
    return false;
}

// Scrub masked-shaped values out of the form state on load so the user
// doesn't accidentally re-submit a mask as the real credential.
function clearMaskedSecrets(cfg: ProvidersConfig): ProvidersConfig {
    const scrubbed = structuredClone(cfg);
    const aws = scrubbed.cloud?.aws as any;
    if (aws) {
        if (isMaskedSecret(aws.access_key_id)) aws.access_key_id = "";
        if (isMaskedSecret(aws.secret_access_key)) aws.secret_access_key = "";
    }
    const gcp = scrubbed.cloud?.gcp as any;
    if (gcp && isMaskedSecret(gcp.service_account_json)) gcp.service_account_json = "";
    const nosana = scrubbed.depin?.nosana as any;
    if (nosana && isMaskedSecret(nosana.wallet_private_key)) nosana.wallet_private_key = "";
    const akash = scrubbed.depin?.akash as any;
    if (akash && isMaskedSecret(akash.mnemonic)) akash.mnemonic = "";
    return scrubbed;
}

type State = {
    config: ProvidersConfig;
    loading: boolean;
    saving: boolean;
    isEditing: boolean;
    isConfigured: boolean;
    nosanaApiKeys: NosanaApiKeyResponse[];
    hfTokens: HfTokenResponse[];
    showAddKeyModal: boolean;
    newKeyName: string;
    newKeyValue: string;
    loadingKeys: boolean;
    showDeleteModal: boolean;
    keyToDelete: string | null;
    hfTokenFromEnv: boolean;
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
    hfTokens: [],
    showAddKeyModal: false,
    newKeyName: "",
    newKeyValue: "",
    loadingKeys: false,
    showDeleteModal: false,
    keyToDelete: null,
    hfTokenFromEnv: false,
};

export default function ProviderConfigPage() {
    const { category, providerId } = useParams();
    const navigate = useNavigate();
    const [state, dispatch] = useReducer(reducer, initialState);
    const {
        config, loading, saving, isEditing, isConfigured,
        nosanaApiKeys, hfTokens, showAddKeyModal, newKeyName, newKeyValue, loadingKeys,
        showDeleteModal, keyToDelete, hfTokenFromEnv
    } = state;

    const isHf = providerId === "huggingface-token";

    useEffect(() => {
        loadConfig();
        if (providerId === "nosana") {
            loadNosanaApiKeys();
        } else if (providerId === "huggingface-token") {
            loadHfTokens();
        }
    }, [providerId]);

    const loadConfig = async () => {
        try {
            const fullResp = await ConfigService.getProviderConfigFull();
            const data = fullResp.providers;
            const envFlag = fullResp.hf_token_from_env ?? false;

            // Merge with initial to ensure structure exists
            const merged = {
                cloud: {
                    aws: { ...initialProviderConfig.cloud.aws, ...data.cloud?.aws },
                    gcp: { ...initialProviderConfig.cloud.gcp, ...data.cloud?.gcp }
                },
                depin: {
                    nosana: { ...initialProviderConfig.depin.nosana, ...data.depin?.nosana },
                    akash: { ...initialProviderConfig.depin.akash, ...data.depin?.akash }
                },
                huggingface: {
                    ...initialProviderConfig.huggingface,
                    ...(data.huggingface ?? {})
                }
            };
            // Determine configured state from the merged server response
            // (which still carries masked values like "AKIA...XYZ8") BEFORE
            // we scrub. Otherwise an isConfigured pool would look unconfigured.
            const configured = checkConfigured(merged, providerId, envFlag);

            // Scrub masked secret values from the form state so the user can't
            // accidentally round-trip the literal mask back as the real key.
            const scrubbed = clearMaskedSecrets(merged);
            dispatch({ type: 'SET_FIELD', field: 'config', value: scrubbed });
            dispatch({ type: 'SET_FIELD', field: 'hfTokenFromEnv', value: envFlag });

            if (configured) {
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

            // Don't sync api_keys into the main config state — they are managed
            // separately via addNosanaApiKey/deleteNosanaApiKey and should not be
            // included when saving the provider config form, as the backend merge
            // would overwrite real keys with placeholder values.
        } catch (e) {
            toast.error("Failed to load Nosana API keys");
        } finally {
            dispatch({ type: 'SET_FIELD', field: 'loadingKeys', value: false });
        }
    };

    const loadHfTokens = async () => {
        try {
            dispatch({ type: 'SET_FIELD', field: 'loadingKeys', value: true });
            const tokens = await ConfigService.listHfTokens();
            dispatch({ type: 'SET_FIELD', field: 'hfTokens', value: tokens });

            // HF tokens are managed separately via addHfToken/deleteHfToken and
            // are NOT synced into the form config state, so the page-level Save
            // never round-trips masked/stale values back over them.
        } catch (e) {
            toast.error("Failed to load HuggingFace tokens");
        } finally {
            dispatch({ type: 'SET_FIELD', field: 'loadingKeys', value: false });
        }
    };

    const handleAddApiKey = async () => {
        const noun = isHf ? "token" : "API key";
        if (!newKeyName.trim() || !newKeyValue.trim()) {
            toast.error(`Please provide both a name and a ${noun}`);
            return;
        }
        try {
            if (isHf) {
                await ConfigService.addHfToken(newKeyName.trim(), newKeyValue.trim());
            } else {
                await ConfigService.addNosanaApiKey(newKeyName.trim(), newKeyValue.trim());
            }
            toast.success(`${isHf ? "Token" : "API key"} "${newKeyName}" added successfully`);
            dispatch({ type: 'RESET_ADD_KEY_MODAL' });
            if (isHf) loadHfTokens(); else loadNosanaApiKeys();
        } catch (e) {
            const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail || `Failed to add ${noun}`);
        }
    };

    const handleDeleteApiKey = async (name: string) => {
        dispatch({ type: 'SHOW_DELETE_MODAL', value: name });
    };

    const confirmDeleteApiKey = async () => {
        if (!keyToDelete) return;
        const name = keyToDelete;
        try {
            if (isHf) {
                await ConfigService.deleteHfToken(name);
            } else {
                await ConfigService.deleteNosanaApiKey(name);
            }
            toast.success(`${isHf ? "Token" : "API key"} "${name}" deleted successfully`);
            dispatch({ type: 'HIDE_DELETE_MODAL' });
            if (isHf) loadHfTokens(); else loadNosanaApiKeys();
        } catch (e) {
            toast.error(`Failed to delete ${isHf ? "token" : "API key"}`);
        }
    };

    const checkConfigured = (data: ProvidersConfig, pid?: string, envFlag?: boolean) => {
        if (!pid) return false;
        switch (pid) {
            case "aws": return !!data.cloud.aws.access_key_id;
            case "gcp": return !!data.cloud.gcp?.project_id || !!data.cloud.gcp?.service_account_json;
            case "nosana": return !!data.depin.nosana.wallet_private_key || !!data.depin.nosana.api_key || (nosanaApiKeys && nosanaApiKeys.length > 0);
            case "akash": return !!data.depin.akash.mnemonic;
            case "huggingface-token": return !!data.huggingface?.token || !!envFlag || (hfTokens && hfTokens.length > 0);
            default: return false;
        }
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        dispatch({ type: 'SET_FIELD', field: 'saving', value: true });
        try {
            // Strip separately-managed credential lists (nosana api_keys,
            // huggingface tokens) — they are persisted via their own add/delete
            // endpoints, and including them here would overwrite real values
            // with masked/stale ones on the page-level Save.
            const safeConfig = structuredClone(config);
            delete (safeConfig.depin.nosana as any).api_keys;
            delete (safeConfig.huggingface as any).tokens;
            await ConfigService.updateProviderConfig(safeConfig);
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
                            isConfigured={isConfigured}
                            nosanaApiKeys={nosanaApiKeys}
                            hfTokens={hfTokens}
                            loadingKeys={loadingKeys}
                            handleAddKey={() => dispatch({ type: 'SET_FIELD', field: 'showAddKeyModal', value: true })}
                            handleDeleteApiKey={handleDeleteApiKey}
                            hfTokenFromEnv={hfTokenFromEnv}
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

            {providerId === "aws" && <EngineAmiSection />}

            {showAddKeyModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-card border rounded-xl p-6 w-full max-w-md mx-4 space-y-4">
                        <h3 className="text-lg font-semibold">{isHf ? "Add HuggingFace Token" : "Add Nosana API Key"}</h3>
                        <div className="space-y-3">
                            <div className="space-y-2">
                                <label htmlFor="key-name" className="text-sm font-medium">{isHf ? "Token Name" : "Key Name"}</label>
                                <input
                                    id="key-name"
                                    value={newKeyName}
                                    onChange={(e) => dispatch({ type: 'SET_FIELD', field: 'newKeyName', value: e.target.value })}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    placeholder={isHf ? "e.g., default, prod, gated-models..." : "e.g., Piyush, Jesse, Production..."}
                                />
                                <p className="text-xs text-muted-foreground">
                                    {isHf ? "A friendly name to pick this token when deploying" : "A friendly name to identify this key"}
                                </p>
                            </div>
                            <div className="space-y-2">
                                <label htmlFor="key-value" className="text-sm font-medium">{isHf ? "HuggingFace Token" : "API Key"}</label>
                                <input
                                    id="key-value"
                                    type="password"
                                    autoComplete="new-password"
                                    value={newKeyValue}
                                    onChange={(e) => dispatch({ type: 'SET_FIELD', field: 'newKeyValue', value: e.target.value })}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                                    placeholder={isHf ? "hf_..." : "nos_..."}
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
                                {isHf ? "Add Token" : "Add Key"}
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
                            <h3 className="text-lg font-semibold">{isHf ? "Delete HuggingFace Token?" : "Delete API Key?"}</h3>
                        </div>

                        <div className="space-y-3">
                            <p className="text-sm text-foreground">
                                Are you sure you want to delete the {isHf ? "token" : "API key"} <span className="font-bold">&quot;{keyToDelete}&quot;</span>?
                            </p>

                            {isHf ? (
                                <div className="p-4 bg-amber-50 border border-amber-100 rounded-lg space-y-2">
                                    <p className="text-xs text-amber-800 leading-relaxed">
                                        This token will no longer be available for new deployments. Deployments already running are unaffected. This action cannot be undone.
                                    </p>
                                </div>
                            ) : (
                                <div className="p-4 bg-red-50 border border-red-100 rounded-lg space-y-2">
                                    <p className="text-xs font-bold text-red-800 flex items-center gap-1.5">
                                        <ShieldCheck className="w-3.5 h-3.5" />
                                        WARNING: RECURSIVE DELETION
                                    </p>
                                    <p className="text-xs text-red-700 leading-relaxed">
                                        Deleting this key will automatically <span className="font-bold underline">terminate all active deployments</span> and <span className="font-bold underline">delete all compute pools</span> associated with it. This action cannot be undone.
                                    </p>
                                </div>
                            )}
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
                                {isHf ? "Delete Token" : "Delete Key & Resources"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function AWSFields({ config, updateField, isConfigured }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void; isConfigured?: boolean }) {
    // When the panel was loaded with stored credentials, the form starts empty
    // (masked values are stripped on load). Tell the user explicitly so they
    // don't think the existing credentials were wiped.
    const accessEmpty = !config.cloud.aws.access_key_id;
    const secretEmpty = !config.cloud.aws.secret_access_key;
    return (
        <>
            {isConfigured && accessEmpty && secretEmpty && (
                <div className="p-3 bg-blue-50 border border-blue-100 rounded-lg text-xs text-blue-700">
                    AWS credentials are stored and masked for security. Re-enter the
                    Access Key ID and Secret Access Key to change them; leave blank
                    to keep the existing values.
                </div>
            )}
            <div className="space-y-2">
                <label htmlFor="aws-access-key" className="text-sm font-medium">Access Key ID</label>
                <input
                    id="aws-access-key"
                    value={config.cloud.aws.access_key_id || ""}
                    onChange={(e) => updateField(['cloud', 'aws', 'access_key_id'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder={isConfigured ? "(unchanged — type to replace)" : "AKIA..."}
                    autoComplete="off"
                />
                <CredHint>
                    AWS Console → <strong>IAM</strong> → Users → your IAM user →
                    Security credentials → <em>Create access key</em>. Starts with <code>AKIA</code>.
                </CredHint>
            </div>
            <div className="space-y-2">
                <label htmlFor="aws-secret-key" className="text-sm font-medium">Secret Access Key</label>
                <input
                    id="aws-secret-key"
                    type="password"
                    value={config.cloud.aws.secret_access_key || ""}
                    onChange={(e) => updateField(['cloud', 'aws', 'secret_access_key'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder={isConfigured ? "(unchanged — type to replace)" : "********"}
                    autoComplete="new-password"
                />
                <CredHint>
                    Shown <strong>only once</strong> on the same screen where you create
                    the access key. If you lost it, generate a new key pair —
                    AWS doesn't let you retrieve the secret.
                </CredHint>
            </div>
        </>
    );
}

function GCPFields({ config, updateField }: { config: ProvidersConfig; updateField: (path: string[], value: any) => void }) {
    return (
        <div className="space-y-4">
            <div className="p-3 bg-blue-50 border border-blue-100 rounded-lg text-xs text-blue-700">
                GCP uses Pulumi for cluster orchestration. Configure your GCP credentials below.
                Pulumi will use your default GCP credentials if service account JSON is not provided.
            </div>
            <div className="space-y-2">
                <label htmlFor="gcp-project" className="text-sm font-medium">Project ID</label>
                <input
                    id="gcp-project"
                    value={config.cloud.gcp?.project_id || ""}
                    onChange={(e) => updateField(['cloud', 'gcp', 'project_id'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="my-gcp-project"
                />
                <CredHint>
                    GCP Console → click the <strong>project picker</strong> at the very
                    top of the page. The Project ID is the string under the project name
                    (e.g. <code>my-project-12345</code>), not the human-readable name.
                </CredHint>
            </div>
            <div className="space-y-2">
                <label htmlFor="gcp-region" className="text-sm font-medium">Default Region</label>
                <input
                    id="gcp-region"
                    value={config.cloud.gcp?.region || "us-central1"}
                    onChange={(e) => updateField(['cloud', 'gcp', 'region'], e.target.value)}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    placeholder="us-central1"
                />
                <CredHint>
                    Any Compute Engine region: <code>us-central1</code>,
                    <code> europe-west4</code>, <code>asia-east1</code>… GPU
                    availability varies — check Compute Engine → Quotas before picking.
                </CredHint>
            </div>
            <div className="space-y-2">
                <label htmlFor="gcp-sa-json" className="text-sm font-medium">Service Account JSON (Optional)</label>
                <textarea
                    id="gcp-sa-json"
                    value={config.cloud.gcp?.service_account_json || ""}
                    onChange={(e) => updateField(['cloud', 'gcp', 'service_account_json'], e.target.value)}
                    className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[100px] font-mono text-xs"
                    placeholder='{"type": "service_account", ...}'
                />
                <CredHint>
                    IAM &amp; Admin → <strong>Service Accounts</strong> → pick a
                    service account → Keys tab → <em>Add Key → JSON</em>. Paste the
                    downloaded file verbatim. Blank ⇒ Pulumi uses your local
                    <code> gcloud auth application-default login</code>.
                </CredHint>
            </div>
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
                <div className="p-3 bg-ember-50 border border-ember-100 rounded-lg text-xs text-ember-700">
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
                <div className="p-3 bg-ember-50 border border-ember-100 rounded-lg text-xs text-ember-700">
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

function HuggingFaceFields({
    hfTokens,
    loadingKeys,
    handleAddKey,
    handleDeleteApiKey,
    hfTokenFromEnv,
}: {
    hfTokens: HfTokenResponse[];
    loadingKeys: boolean;
    handleAddKey: () => void;
    handleDeleteApiKey: (name: string) => void;
    hfTokenFromEnv?: boolean;
}) {
    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold flex items-center gap-2">
                    <Key className="w-4 h-4" />
                    HuggingFace Tokens
                </h3>
                <button
                    type="button"
                    onClick={handleAddKey}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                >
                    <Plus className="w-3.5 h-3.5" />
                    Add Token
                </button>
            </div>

            <p className="text-xs text-muted-foreground">
                Add multiple HuggingFace tokens with friendly names.
                Pick one by name when deploying gated models.
            </p>

            {hfTokenFromEnv && (
                <div className="p-3 bg-ember-50 border border-ember-100 rounded-lg text-xs text-ember-700">
                    A default token is also provided via the <code>INFERIA_HF_TOKEN</code> environment variable
                    and will be used as a fallback when no named token is selected.
                </div>
            )}

            {loadingKeys ? (
                <div className="text-center py-4 text-muted-foreground text-sm">
                    Loading tokens...
                </div>
            ) : hfTokens.length === 0 ? (
                <div className="text-center py-6 bg-muted/30 rounded-lg border border-dashed">
                    <Key className="w-8 h-8 mx-auto text-muted-foreground mb-2" />
                    <p className="text-sm text-muted-foreground">No tokens configured</p>
                    <p className="text-xs text-muted-foreground mt-1">Add your first token to deploy gated models</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {hfTokens.map((t) => (
                        <div
                            key={t.name}
                            className="flex items-center justify-between p-3 bg-muted/30 rounded-lg border"
                        >
                            <div className="flex items-center gap-3">
                                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                    <Key className="w-4 h-4 text-primary" />
                                </div>
                                <div>
                                    <p className="font-medium text-sm">{t.name}</p>
                                    <p className="text-xs text-muted-foreground">
                                        {t.is_active ? 'Active' : 'Inactive'}
                                    </p>
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={() => handleDeleteApiKey(t.name)}
                                className="p-2 text-muted-foreground hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
                                title="Delete token"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

function ProviderFormFields({
    providerId,
    config,
    updateField,
    isConfigured,
    nosanaApiKeys,
    hfTokens,
    loadingKeys,
    handleAddKey,
    handleDeleteApiKey,
    hfTokenFromEnv,
}: {
    providerId?: string;
    config: ProvidersConfig;
    updateField: (path: string[], value: any) => void;
    isConfigured?: boolean;
    nosanaApiKeys: NosanaApiKeyResponse[];
    hfTokens: HfTokenResponse[];
    loadingKeys: boolean;
    handleAddKey: () => void;
    handleDeleteApiKey: (name: string) => void;
    hfTokenFromEnv?: boolean;
}) {
    switch (providerId) {
        case "aws":
            return <AWSFields config={config} updateField={updateField} isConfigured={isConfigured} />;
        case "gcp":
            return <GCPFields config={config} updateField={updateField} />;
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
        case "huggingface-token":
            return (
                <HuggingFaceFields
                    hfTokens={hfTokens}
                    loadingKeys={loadingKeys}
                    handleAddKey={handleAddKey}
                    handleDeleteApiKey={handleDeleteApiKey}
                    hfTokenFromEnv={hfTokenFromEnv}
                />
            );
        default:
            return <div>Unknown Provider</div>;
    }
}
