
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { authService, type OrganizationBasicInfo } from "@/services/authService";
import { getToken, setToken, clearToken } from "@/lib/tokenStore";

interface User {
    user_id: string;
    username: string;
    email: string;
    roles: string[];
    permissions: string[];
    org_id?: string;
    totp_enabled: boolean;
}

interface AuthContextType {
    user: User | null;
    isLoading: boolean;
    login: (token: string) => Promise<void>;
    logout: () => void;
    isAuthenticated: boolean;
    organizations: OrganizationBasicInfo[];
    hasPermission: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [organizations, setOrganizations] = useState<OrganizationBasicInfo[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    const fetchUser = async () => {
        try {
            const { data } = await api.get("/auth/me");
            setUser(data);

            // Fetch Orgs
            const orgs = await authService.getOrganizations();
            setOrganizations(orgs);
        } catch (error) {
            console.error("Failed to fetch user", error);
            logout(); // Invalid token
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        // Token lives in memory only — a page refresh means re-login
        if (getToken()) {
            fetchUser();
        } else {
            setIsLoading(false);
        }
    }, []);

    const login = async (token: string) => {
        setToken(token);
        await fetchUser();
        toast.success("Welcome back!");
    };

    const logout = () => {
        clearToken();
        setUser(null);
        setOrganizations([]);
        toast.info("Logged out");
    };

    const hasPermission = (permission: string) => {
        if (!user) return false;
        return user.permissions.includes(permission);
    };

    return (
        <AuthContext.Provider
            value={{
                user,
                isLoading,
                login,
                logout,
                isAuthenticated: !!user,
                organizations,
                hasPermission,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
}
