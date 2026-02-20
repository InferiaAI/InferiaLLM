import { useCallback, useEffect, useMemo, useReducer } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { Scale, Save, Activity, Shield, RefreshCw } from "lucide-react";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import type { AxiosError } from "axios";

interface ConfigResponse {
  policy_type: string;
  config_json: {
    request_limit?: number;
    token_limit?: number;
  };
}

interface OrganizationData {
  id: string;
  name: string;
  log_payloads: boolean;
}

interface UsageStat {
  key_name: string;
  key_prefix: string;
  requests: number;
  tokens: number;
}

type ApiErrorResponse = {
  detail?: string;
};

const DEFAULT_REQUEST_LIMIT = 1000;
const DEFAULT_TOKEN_LIMIT = 100000;

type State = {
  isLoading: boolean;
  isSaving: boolean;
  requestLimit: number;
  tokenLimit: number;
  usageStats: UsageStat[];
  orgData: OrganizationData | null;
};

type Action =
  | { type: 'SET_FIELD'; field: keyof State; value: any };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value };
    default:
      return state;
  }
}

const initialState: State = {
  isLoading: true,
  isSaving: false,
  requestLimit: DEFAULT_REQUEST_LIMIT,
  tokenLimit: DEFAULT_TOKEN_LIMIT,
  usageStats: [],
  orgData: null,
};

export default function Organization() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const {
    isLoading,
    isSaving,
    requestLimit,
    tokenLimit,
    usageStats,
    orgData,
  } = state;

  const isQuotaInvalid = useMemo(() => requestLimit < 1 || tokenLimit < 1, [requestLimit, tokenLimit]);

  const fetchOrgData = useCallback(async () => {
    try {
      const { data } = await api.get<OrganizationData>("/management/organizations/me");
      dispatch({ type: 'SET_FIELD', field: 'orgData', value: data });
    } catch (error) {
      console.error("Failed to fetch organization data:", error);
    }
  }, []);

  const fetchUsageStats = useCallback(async () => {
    try {
      const { data } = await api.get<UsageStat[]>("/management/config/quota/usage");
      dispatch({ type: 'SET_FIELD', field: 'usageStats', value: data });
    } catch (error) {
      console.error("Failed to fetch usage stats:", error);
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const { data } = await api.get<ConfigResponse>("/management/config/quota");
      dispatch({ type: 'SET_FIELD', field: 'requestLimit', value: data.config_json?.request_limit ?? DEFAULT_REQUEST_LIMIT });
      dispatch({ type: 'SET_FIELD', field: 'tokenLimit', value: data.config_json?.token_limit ?? DEFAULT_TOKEN_LIMIT });
    } catch (error) {
      console.error("Failed to fetch quota config:", error);
      dispatch({ type: 'SET_FIELD', field: 'requestLimit', value: DEFAULT_REQUEST_LIMIT });
      dispatch({ type: 'SET_FIELD', field: 'tokenLimit', value: DEFAULT_TOKEN_LIMIT });
    }
  }, []);

  const fetchAll = useCallback(async () => {
    dispatch({ type: 'SET_FIELD', field: 'isLoading', value: true });
    await Promise.all([fetchConfig(), fetchUsageStats(), fetchOrgData()]);
    dispatch({ type: 'SET_FIELD', field: 'isLoading', value: false });
  }, [fetchConfig, fetchUsageStats, fetchOrgData]);

  const handleUpdateLogPayloads = async (enabled: boolean) => {
    if (!orgData) return;
    try {
      dispatch({ type: 'SET_FIELD', field: 'orgData', value: { ...orgData, log_payloads: enabled } });
      await api.patch("/management/organizations/me", { log_payloads: enabled });
      toast.success(`Inference payload logging ${enabled ? "enabled" : "disabled"}`);
    } catch (error) {
      console.error(error);
      toast.error("Failed to update logging preference");
      await fetchOrgData();
    }
  };

  const handleSave = async () => {
    if (isQuotaInvalid) {
      toast.error("Quota values must be positive numbers");
      return;
    }

    dispatch({ type: 'SET_FIELD', field: 'isSaving', value: true });
    try {
      await api.post("/management/config", {
        policy_type: "quota",
        config_json: {
          request_limit: requestLimit,
          token_limit: tokenLimit,
        },
      });
      toast.success("Organization quota updated successfully");
    } catch (error) {
      const apiError = error as AxiosError<ApiErrorResponse>;
      toast.error(apiError.response?.data?.detail || "Failed to update quota");
    } finally {
      dispatch({ type: 'SET_FIELD', field: 'isSaving', value: false });
    }
  };

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  if (isLoading) return <LoadingScreen message="Loading organization settings..." />;

  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Organization Settings</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Manage quotas, privacy defaults, and usage visibility for your organization.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void fetchAll()}
            className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      <div className="bg-card rounded-lg border shadow-sm p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider flex items-center gap-2 text-muted-foreground">
            <Scale className="w-4 h-4" /> Quota Management
          </h2>
          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving || isQuotaInvalid}
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {isSaving ? "Saving..." : "Save Changes"}
          </button>
        </div>

        <div className="space-y-6">
          <div className="p-4 bg-muted/30 border rounded-lg text-sm text-foreground">
            Define usage limits for all users and deployments within this organization.
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-3">
              <label htmlFor="request-limit" className="text-sm font-medium">Daily Request Limit</label>
              <input
                id="request-limit"
                type="number"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                min={1}
                value={requestLimit}
                onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'requestLimit', value: Number(event.target.value) })}
              />
              <p className="text-xs text-muted-foreground">Maximum number of inference requests per day across the organization.</p>
            </div>

            <div className="space-y-3">
              <label htmlFor="token-limit" className="text-sm font-medium">Daily Token Limit</label>
              <input
                id="token-limit"
                type="number"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                min={1}
                value={tokenLimit}
                onChange={(event) => dispatch({ type: 'SET_FIELD', field: 'tokenLimit', value: Number(event.target.value) })}
              />
              <p className="text-xs text-muted-foreground">Maximum total tokens (prompt + completion) per day.</p>
            </div>
          </div>

          {isQuotaInvalid && (
            <p className="text-xs text-destructive">Both quota fields must be greater than 0.</p>
          )}
        </div>
      </div>

      <div className="bg-card rounded-lg border shadow-sm p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wider flex items-center gap-2 text-muted-foreground mb-6">
          <Shield className="w-4 h-4" /> Privacy & Data
        </h2>

        <div className="space-y-6">
          <div className="flex items-center justify-between p-4 bg-muted/30 border rounded-lg gap-4">
            <div className="space-y-1">
              <div className="text-sm font-medium">Log Inference Payloads</div>
              <div className="text-xs text-muted-foreground max-w-2xl">
                If enabled, full prompt and response content will be stored in inference logs. Disable this to keep only metadata and performance metrics.
              </div>
            </div>
            <button
              type="button"
              onClick={() => void handleUpdateLogPayloads(!orgData?.log_payloads)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${orgData?.log_payloads ? "bg-primary" : "bg-muted"
                }`}
              aria-label="Toggle inference payload logging"
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${orgData?.log_payloads ? "translate-x-6" : "translate-x-1"
                  }`}
              />
            </button>
          </div>
        </div>
      </div>

      <div className="bg-card rounded-lg border shadow-sm p-6">
        <h2 className="text-sm font-semibold uppercase tracking-wider flex items-center gap-2 text-muted-foreground mb-6">
          <Activity className="w-4 h-4" /> Usage Statistics (Today)
        </h2>

        <div className="rounded-md border overflow-hidden">
          <table className="w-full text-sm text-left">
            <thead className="bg-muted/50 text-muted-foreground font-medium">
              <tr className="border-b">
                <th className="px-4 py-3 font-medium">Key Name</th>
                <th className="px-4 py-3 font-medium">Prefix</th>
                <th className="px-4 py-3 font-medium">Requests</th>
                <th className="px-4 py-3 font-medium">Tokens</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {usageStats.map((stat) => (
                <tr key={stat.key_prefix} className="hover:bg-muted/50 transition-colors">
                  <td className="px-4 py-3 font-medium">{stat.key_name}</td>
                  <td className="px-4 py-3 font-mono text-xs">{stat.key_prefix}</td>
                  <td className="px-4 py-3">{stat.requests.toLocaleString()}</td>
                  <td className="px-4 py-3">{stat.tokens.toLocaleString()}</td>
                </tr>
              ))}
              {usageStats.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                    No usage recorded today.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
