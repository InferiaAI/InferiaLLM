import { Navigate, Outlet, useLocation, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { ShieldAlert } from "lucide-react";

interface PermissionGuardProps {
    permission: string;
    redirectTo?: string;
}

export function PermissionGuard({ permission, redirectTo = "/dashboard" }: PermissionGuardProps) {
    const { hasPermission, isLoading, isAuthenticated } = useAuth();
    const location = useLocation();

    if (isLoading) {
        return <LoadingScreen message="Checking permissions..." />;
    }

    if (!isAuthenticated) {
        return <Navigate to="/auth/login" replace />;
    }

    if (!hasPermission(permission)) {
        return (
            <div className="min-h-[60vh] flex items-center justify-center p-6">
                <div className="max-w-lg w-full rounded-xl border bg-card p-6 shadow-sm space-y-3">
                    <div className="inline-flex items-center gap-2 rounded-md bg-amber-500/10 text-amber-700 dark:text-amber-300 px-3 py-1.5 text-sm font-medium">
                        <ShieldAlert className="h-4 w-4" />
                        Access Denied
                    </div>
                    <h2 className="text-xl font-semibold tracking-tight">You do not have access to this page</h2>
                    <p className="text-sm text-muted-foreground">
                        Required permission: <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{permission}</code>
                    </p>
                    <p className="text-sm text-muted-foreground break-all">
                        Requested path: <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{location.pathname}</code>
                    </p>
                    <div className="pt-2">
                        <Link
                            to={redirectTo}
                            className="inline-flex items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
                        >
                            Go to Dashboard
                        </Link>
                    </div>
                </div>
            </div>
        );
    }

    return <Outlet />;
}
