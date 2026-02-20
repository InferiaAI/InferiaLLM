
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { LoadingScreen } from "@/components/ui/LoadingScreen";

interface PermissionGuardProps {
    permission: string;
    redirectTo?: string;
}

export function PermissionGuard({ permission, redirectTo = "/dashboard" }: PermissionGuardProps) {
    const { hasPermission, isLoading, isAuthenticated } = useAuth();

    if (isLoading) {
        return <LoadingScreen message="Checking permissions..." />;
    }

    if (!isAuthenticated) {
        return <Navigate to="/auth/login" replace />;
    }

    if (!hasPermission(permission)) {
        return <Navigate to={redirectTo} replace />;
    }

    return <Outlet />;
}
