
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { authService, type OrganizationBasicInfo } from "@/services/authService";

interface User {
    user_id: string;
    username: string;
    email: string;
    roles: string[];
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
        const token = localStorage.getItem("token");
        if (token) {
            fetchUser();
        } else {
            setIsLoading(false);
        }
    }, []);

    const login = async (token: string) => {
        localStorage.setItem("token", token);
        await fetchUser();
        toast.success("Welcome back!");
    };


    const logout = () => {
        localStorage.removeItem("token");
        setUser(null);
        setOrganizations([]);
        toast.info("Logged out");
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
