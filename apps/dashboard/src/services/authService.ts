import api from "@/lib/api";

export interface RegisterRequest {
    email: string;
    password: string;
    organization_name?: string;
    invite_token?: string;
}

export interface InviteInfo {
    email: string;
    role: string;
    token: string;
    invite_link: string;
    status: string;
    expires_at: string;
}

export interface OrganizationBasicInfo {
    id: string;
    name: string;
    role: string;
}

export interface AuthResponse {
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
    organizations?: OrganizationBasicInfo[];
}

export const authService = {
    login: async (credentials: any) => {
        const { data } = await api.post<AuthResponse>("/auth/login", credentials);
        return data;
    },
    register: async (credentials: RegisterRequest) => {
        const { data } = await api.post<AuthResponse>("/auth/register", credentials);
        return data;
    },
    registerInvite: async (payload: { token: string; password: string }) => {
        const { data } = await api.post<AuthResponse>("/auth/register-invite", payload);
        return data;
    },
    getInviteInfo: async (token: string) => {
        const { data } = await api.get<InviteInfo>(`/auth/invitations/${token}`);
        return data;
    },
    acceptInvite: async (token: string) => {
        const { data } = await api.post<AuthResponse>(`/auth/accept-invite?token=${token}`);
        if (data.access_token) {
            localStorage.setItem("token", data.access_token);
            if (data.refresh_token) {
                localStorage.setItem("refresh_token", data.refresh_token);
            }
        }
        return data;
    },
    switchOrg: async (orgId: string) => {
        const { data } = await api.post<AuthResponse>("/auth/switch-org", { org_id: orgId });
        return data;
    },
    getOrganizations: async (params?: { skip?: number; limit?: number }) => {
        const { data } = await api.get<OrganizationBasicInfo[]>("/auth/organizations", { params });
        return data;
    }
};
