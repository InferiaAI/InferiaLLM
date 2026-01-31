import api from "@/lib/api";

export interface AuditLog {
    id: string;
    timestamp: string;
    user_id: string | null;
    action: string;
    resource_type: string | null;
    resource_id: string | null;
    details: Record<string, any> | null;
    ip_address: string | null;
    status: string;
}

export interface AuditLogFilter {
    user_id?: string;
    action?: string;
    resource_type?: string;
    start_date?: string;
    end_date?: string;
    status?: string;
}

export const auditService = {
    getLogs: async (filters?: AuditLogFilter) => {
        // Convert filters to query params
        const params = new URLSearchParams();
        if (filters) {
            Object.entries(filters).forEach(([key, value]) => {
                if (value) params.append(key, value);
            });
        }

        const { data } = await api.get<AuditLog[]>("/audit/logs", { params });
        return data;
    }
};
