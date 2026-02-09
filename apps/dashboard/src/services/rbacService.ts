import api from "@/lib/api";

export interface Role {
    name: string;
    description?: string;
    permissions: string[];
    created_at: string;
}

export interface RoleCreate {
    name: string;
    description?: string;
    permissions: string[];
}

export interface User {
    id: string;
    email: string;
    role: string;
    created_at: string;
}

export interface UserRoleUpdate {
    role: string;
}

export interface Invitation {
    id: string;
    email: string;
    role: string;
    token: string;
    invite_link: string;
    status: string;
    expires_at: string;
    created_at: string;
}

export interface PaginationParams {
    skip?: number;
    limit?: number;
}

export const rbacService = {
    // Roles
    getRoles: async (params?: PaginationParams) => {
        const { data } = await api.get<Role[]>("/admin/roles", { params });
        return data;
    },
    createRole: async (role: RoleCreate) => {
        const { data } = await api.post<Role>("/admin/roles", role);
        return data;
    },
    updateRole: async (name: string, role: Partial<RoleCreate>) => {
        const { data } = await api.put<Role>(`/admin/roles/${name}`, role);
        return data;
    },
    deleteRole: async (name: string) => {
        const { data } = await api.delete(`/admin/roles/${name}`);
        return data;
    },
    getPermissions: async () => {
        const { data } = await api.get<string[]>("/admin/roles/permissions/list");
        return data;
    },

    // Users
    getUsers: async (params?: PaginationParams) => {
        const { data } = await api.get<User[]>("/management/users", { params });
        return data;
    },
    updateUserRole: async (userId: string, roleName: string) => {
        const { data } = await api.put(`/admin/users/${userId}/role`, { role: roleName });
        return data;
    },

    // Invitations
    getInvitations: async (params?: PaginationParams) => {
        const { data } = await api.get<{ invitations: Invitation[] }>("/management/invitations", { params });
        return data.invitations;
    },
    inviteUser: async (email: string, role: string) => {
        const { data } = await api.post<Invitation>("/management/invitations", { email, role });
        return data;
    },
    revokeInvitation: async (id: string) => {
        await api.delete(`/management/invitations/${id}`);
    }
};
