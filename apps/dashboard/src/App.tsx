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

import Deployments from "@/pages/Deployments";
import NewDeployment from "@/pages/NewDeployment";
import DeploymentDetail from "@/pages/DeploymentDetail";
import ApiKeys from "@/pages/ApiKeys";
import Templates from "@/pages/Templates";
import KnowledgeBase from "@/pages/KnowledgeBase";
import Roles from "@/pages/Settings/Roles";
import Users from "@/pages/Settings/Users";
import Organization from "@/pages/Settings/Organization";
import AuditLogs from "@/pages/Settings/AuditLogs";
import Security from "@/pages/Settings/Security";
import ProviderCategories from "@/pages/Settings/Providers/ProviderCategories";
import ProviderList from "@/pages/Settings/Providers/ProviderList";
import ProviderConfigPage from "@/pages/Settings/Providers/ProviderConfig";
import Status from "@/pages/Status";
import Instances from "@/pages/Compute/Instances";
import InstanceDetail from "@/pages/Compute/InstanceDetail";
import NewPool from "@/pages/Compute/NewPool";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "sonner";

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
            index: true,
            element: <Overview />,
          },
          {
            path: "deployments",
            element: <Deployments />,
          },
          {
            path: "deployments/new",
            element: <NewDeployment />,
          },
          {
            path: "deployments/:id",
            element: <DeploymentDetail />,
          },
          {
            path: "api-keys",
            element: <ApiKeys />,
          },
          {
            path: "knowledge-base",
            element: <KnowledgeBase />,
          },
          {
            path: "templates",
            element: <Templates />,
          },
          {
            path: "compute/new",
            element: <NewDeployment />,
          },
          {
            path: "compute/pools",
            element: <Instances />,
          },
          {
            path: "compute/pools/new",
            element: <NewPool />,
          },
          {
            path: "compute/pools/:id",
            element: <InstanceDetail />,
          },
          {
            // Legacy / Alias
            path: "compute/instances",
            element: <Instances />,
          },
          {
            path: "settings/roles",
            element: <Roles />,
          },
          {
            path: "settings/users",
            element: <Users />,
          },
          {
            path: "settings/organization",
            element: <Organization />,
          },
          {
            path: "settings/audit-logs",
            element: <AuditLogs />,
          },
          {
            path: "settings/security",
            element: <Security />,
          },
          {
            path: "settings/providers",
            element: <ProviderCategories />,
          },
          {
            path: "settings/providers/:category",
            element: <ProviderList />,
          },
          {
            path: "settings/providers/:category/:providerId",
            element: <ProviderConfigPage />,
          },
          {
            path: "compute",
            element: <Navigate to="instances" replace />,
          },
          {
            path: "settings",
            element: <Navigate to="organization" replace />,
          },
          {
            path: "status",
            element: <Status />,
          },
        ],
      },
    ],
  },
]);

function App() {
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
