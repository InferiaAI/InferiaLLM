
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { authService, type OrganizationBasicInfo } from "@/services/authService";
import {
    getToken,
    setToken,
    clearToken,
    getRefreshToken,
    setRefreshToken,
} from "@/lib/tokenStore";
import { isExternalAuthMode } from "@/lib/authMode";

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
    login: (
        accessToken: string,
        refreshToken: string,
        organizations?: OrganizationBasicInfo[],
    ) => Promise<void>;
    logout: () => void;
    refreshUser: () => Promise<void>;
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
        const init = async () => {
            // 1. Access token still in memory (e.g. soft navigation)
            if (getToken()) {
                await fetchUser();
                return;
            }

            // 2. Page was refreshed — try the refresh token stored in sessionStorage
            const rt = getRefreshToken();
            if (rt) {
                try {
                    const { data } = await api.post("/auth/refresh", null, {
                        headers: { Authorization: `Bearer ${rt}` },
                    });
                    setToken(data.access_token);
                    setRefreshToken(data.refresh_token);
                    await fetchUser();
                    return;
                } catch {
                    // Refresh token expired or invalid — fall through to login
                    clearToken();
                }
            }

            // 3. No usable tokens.
            // In external-auth mode there is no local refresh token — send the
            // browser back through /auth/start so the IdP's SSO cookie silently
            // re-issues a new access token. Only do this when we are on a
            // protected route; /auth/* and /login routes must not be redirected
            // to avoid an infinite loop.
            if (
                isExternalAuthMode() &&
                !window.location.pathname.startsWith("/auth/") &&
                window.location.pathname !== "/login"
            ) {
                window.location.assign("/auth/start");
                return;
            }
            setIsLoading(false);
        };

        void init();
    }, []);

    const login = async (
        accessToken: string,
        refreshToken: string,
        orgs?: OrganizationBasicInfo[],
    ) => {
        setToken(accessToken);
        setRefreshToken(refreshToken);
        // Pre-populate organizations from login response to avoid race condition
        if (orgs?.length) {
            setOrganizations(orgs);
        }
        await fetchUser();
        toast.success("Welcome back!");
    };

    const logout = () => {
        clearToken(); // also clears refresh token
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
                refreshUser: fetchUser,
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
