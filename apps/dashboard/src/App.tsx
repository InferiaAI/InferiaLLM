import {
  createBrowserRouter,
  RouterProvider,
  Navigate,
  Outlet,
} from "react-router-dom";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import DashboardLayout from "@/layouts/DashboardLayout";
import AuthLayout from "@/layouts/AuthLayout";
import Login from "@/pages/Login";
import Register from "@/pages/Register";
import AcceptInvite from "@/pages/AcceptInvite";
import Setup2FA from "@/pages/Auth/Setup2FA";
import Overview from "@/pages/Overview";
import Insights from "@/pages/Insights";
import NotFound from "@/pages/NotFound";

import Deployments from "@/pages/Deployments";
import NewDeployment from "@/pages/NewDeployment";
import DeploymentDetail from "@/pages/DeploymentDetail";
import ApiKeys from "@/pages/ApiKeys";
import Roles from "@/pages/Settings/Roles";
import Users from "@/pages/Settings/Users";
import Organization from "@/pages/Settings/Organization";
import AuditLogs from "@/pages/Settings/AuditLogs";
import Security from "@/pages/Settings/Security";
import ProviderCategories from "@/pages/Settings/Providers/ProviderCategories";
import ProviderList from "@/pages/Settings/Providers/ProviderList";
import ProviderConfigPage from "@/pages/Settings/Providers/ProviderConfig";
import Status from "@/pages/Status";
import Sandbox from "@/pages/Sandbox";
import Models from "@/pages/Models";
import Pools from "@/pages/Compute/Pools";
import PoolDetail from "@/pages/Compute/PoolDetail";
import NewPool from "@/pages/Compute/NewPool";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "sonner";
import { PermissionGuard } from "@/components/PermissionGuard";
import { useTokenFragmentConsumer } from "@/hooks/useTokenFragmentConsumer";

function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <LoadingScreen message="Initializing application..." />;
  if (!isAuthenticated) return <Navigate to="/auth/login" replace />;
  return <Outlet />;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/dashboard" replace />,
  },
  {
    path: "/auth",
    element: <AuthLayout />,
    children: [
      {
        path: "login",
        element: <Login />,
      },
      {
        path: "register",
        element: <Register />,
      },
      {
        path: "accept-invite",
        element: <AcceptInvite />,
      },
      {
        path: "setup-2fa",
        element: <Setup2FA />,
      },
    ],
  },
  {
    path: "/dashboard",
    element: <RequireAuth />,
    children: [
      {
        element: <DashboardLayout />,
        children: [
          {
            element: <PermissionGuard permission="organization:view" />,
            children: [
              {
                index: true,
                element: <Overview />,
              },
            ],
          },
          {
            element: <PermissionGuard permission="deployment:list" />,
            children: [
              {
                path: "insights",
                element: <Insights />,
              },
            ]
          },
          {
            element: <PermissionGuard permission="deployment:list" />,
            children: [
              {
                path: "sandbox",
                element: <Sandbox />,
              },
              {
                path: "deployments",
                element: <Deployments />,
              },
              {
                path: "deployments/:id",
                element: <DeploymentDetail />,
              },
              // Pool-first routes (canonical)
              {
                path: "compute/pools",
                element: <Pools />,
              },
              {
                path: "compute/pools/:id/*",
                element: <PoolDetail />,
              },
              // Legacy node routes — redirect to pools
              {
                path: "compute/nodes",
                element: <Navigate to="/dashboard/compute/pools" replace />,
              },
              {
                path: "compute/nodes/:id",
                element: <Navigate to="/dashboard/compute/pools" replace />,
              },
            ]
          },
          {
            element: <PermissionGuard permission="deployment:create" />,
            children: [
              {
                path: "deployments/new",
                element: <NewDeployment />,
              },
              {
                path: "compute/new",
                element: <NewDeployment />,
              },
              {
                path: "compute/pools/new",
                element: <NewPool />,
              },
              // Legacy redirect
              {
                path: "compute/nodes/new",
                element: <Navigate to="/dashboard/compute/pools/new" replace />,
              },
            ]
          },
          {
            element: <PermissionGuard permission="model:list" />,
            children: [
              { path: "models", element: <Models /> },
            ],
          },
          {
            element: <PermissionGuard permission="api_key:list" />,
            children: [
              {
                path: "api-keys",
                element: <ApiKeys />,
              },
            ]
          },
          {
            path: "settings/roles",
            element: <PermissionGuard permission="role:list" />,
            children: [{ index: true, element: <Roles /> }]
          },
          {
            path: "settings/users",
            element: <PermissionGuard permission="member:list" />,
            children: [{ index: true, element: <Users /> }]
          },
          {
            path: "settings/organization",
            element: <PermissionGuard permission="organization:view" />,
            children: [{ index: true, element: <Organization /> }]
          },
          {
            path: "settings/audit-logs",
            element: <PermissionGuard permission="audit_log:list" />,
            children: [{ index: true, element: <AuditLogs /> }]
          },
          {
            path: "settings/providers",
            element: <PermissionGuard permission="organization:update" />,
            children: [
              { index: true, element: <ProviderCategories /> },
              { path: ":category", element: <ProviderList /> },
              { path: ":category/:providerId", element: <ProviderConfigPage /> },
            ]
          },
          {
            path: "settings/security",
            element: <Security />,
          },
          {
            path: "compute",
            element: <Navigate to="pools" replace />,
          },
          {
            path: "settings",
            element: <Navigate to="organization" replace />,
          },
          {
            path: "status",
            element: <PermissionGuard permission="organization:view" />,
            children: [{ index: true, element: <Status /> }]
          },
        ],
      },
    ],
  },
  {
    path: "*",
    element: <NotFound />,
  },
]);

function App() {
  // Consume `#access_token=<jwt>` left by the gateway's /auth/callback redirect
  // BEFORE AuthProvider's init effect reads the in-memory token. This runs
  // before AuthProvider mounts because hooks fire top-down.
  useTokenFragmentConsumer();

  return (
    <AuthProvider>
      <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
        <Toaster position="top-center" richColors />
        <RouterProvider router={router} />
      </ThemeProvider>
    </AuthProvider>
  );
}

export default App;
