import api from "@/lib/api";

export interface AuditLog {
    id: string;
    timestamp: string;
    user_id: string | null;
    user_email: string | null;
    action: string;
    category: string | null;
    resource_type: string | null;
    resource_id: string | null;
    details: Record<string, any> | null;
    ip_address: string | null;
    status: string;
}

export interface AuditLogFilter {
    user_id?: string;
    action?: string;
    category?: string;
    resource_type?: string;
    start_date?: string;
    end_date?: string;
    status?: string;
}

export interface PaginationParams {
    skip?: number;
    limit?: number;
}

export const AUDIT_CATEGORIES = [
    { value: "", label: "All Categories" },
    { value: "auth", label: "Authentication" },
    { value: "security", label: "Security (2FA)" },
    { value: "deployment", label: "Deployments" },
    { value: "api_key", label: "API Keys" },
    { value: "organization", label: "Organization" },
    { value: "user_management", label: "User Management" },
    { value: "credential", label: "Credentials" },
    { value: "configuration", label: "Configuration" },
] as const;

export const CATEGORY_COLORS: Record<string, string> = {
    auth: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    security: "bg-red-500/10 text-red-600 dark:text-red-400",
    deployment: "bg-ember-500/10 text-ember-600 dark:text-ember-400",
    api_key: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    organization: "bg-purple-500/10 text-purple-600 dark:text-purple-400",
    user_management: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    credential: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
    configuration: "bg-muted-foreground/10 text-muted-foreground",
};

export const auditService = {
    getLogs: async (filters?: AuditLogFilter, pagination?: PaginationParams) => {
        const params = new URLSearchParams();
        if (filters) {
            Object.entries(filters).forEach(([key, value]) => {
                if (value) params.append(key, value);
            });
        }
        if (pagination?.skip !== undefined) {
            params.append("skip", pagination.skip.toString());
        }
        if (pagination?.limit !== undefined) {
            params.append("limit", pagination.limit.toString());
        }

        const { data } = await api.get<AuditLog[]>("/audit/logs", { params });
        // Defensive: if a proxy/edge layer answers with something other than
        // the JSON array (e.g. an SPA index.html fallback), axios still
        // resolves with that body. Surfacing a typed error here keeps the
        // page on its error path instead of crashing on logs.map(...).
        if (!Array.isArray(data)) {
            throw new Error("Unexpected response from /audit/logs (not a list of audit logs)");
        }
        return data;
    }
};
