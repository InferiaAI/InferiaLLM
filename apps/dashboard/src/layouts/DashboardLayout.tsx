import { Outlet, NavLink, useNavigate, useLocation, Navigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import {
  LayoutDashboard,
  Rocket,
  Key,
  LogOut,
  Database,
  Users,
  Shield,
  FileText,
  Menu,
  Search,
  Building2,
  Clock,
  Box,
  Sun,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronRight,
  Activity,
  BarChart3,
  Plus,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";
import { useTheme } from "@/components/theme-provider";
import { SpotlightSearch, useSpotlight } from "@/components/SpotlightSearch";

const navItems: (NavItem & { permission?: string })[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/dashboard/insights", label: "Insights", icon: BarChart3, permission: "audit_log:list" }, // Or similar
  { href: "/dashboard/deployments", label: "Deployments", icon: Rocket, permission: "deployment:list" },
  { href: "/dashboard/compute/pools", label: "Compute Pools", icon: Box, permission: "deployment:list" }, // Reusing deployment list for now
  { href: "/dashboard/templates", label: "Templates", icon: FileText, permission: "prompt_template:list" },
  { href: "/dashboard/knowledge-base", label: "Knowledge Base", icon: Database, permission: "knowledge_base:list" },
  { href: "/dashboard/api-keys", label: "API Keys", icon: Key, permission: "api_key:list" },
];

const settingsItems: (NavItem & { permission?: string })[] = [
  { href: "/dashboard/settings/organization", label: "Organization", icon: Building2, permission: "organization:view" },
  { href: "/dashboard/settings/users", label: "Users", icon: Users, permission: "member:list" },
  { href: "/dashboard/settings/roles", label: "Roles", icon: Shield, permission: "role:list" },
  { href: "/dashboard/settings/audit-logs", label: "Audit Logs", icon: Clock, permission: "audit_log:list" },
  { href: "/dashboard/settings/providers", label: "Providers", icon: Database, permission: "admin:all" },
  { href: "/dashboard/settings/security", label: "Security", icon: Shield },
  { href: "/dashboard/status", label: "System Status", icon: Activity },
];

const breadcrumbLabels: Record<string, string> = {
  dashboard: "Overview",
  insights: "Insights",
  deployments: "Deployments",
  compute: "Compute",
  pools: "Pools",
  templates: "Templates",
  "knowledge-base": "Knowledge Base",
  "api-keys": "API Keys",
  settings: "Settings",
  organization: "Organization",
  users: "Users",
  roles: "Roles",
  "audit-logs": "Audit Logs",
  providers: "Providers",
  security: "Security",
  status: "System Status",
};

function SidebarItem({
  item,
  isCollapsed,
  closeMobile,
}: {
  item: NavItem;
  isCollapsed: boolean;
  closeMobile: () => void;
}) {
  const Icon = item.icon;

  return (
    <NavLink to={item.href} end={item.exact} onClick={closeMobile} title={isCollapsed ? item.label : undefined}>
      {({ isActive }) => (
        <div
          className={cn(
            "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200 group relative",
            isActive
              ? "bg-blue-50 text-blue-700 dark:bg-zinc-800 dark:text-blue-400"
              : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100",
            isCollapsed && "justify-center px-2"
          )}
        >
          <Icon
            className={cn(
              "w-4 h-4 shrink-0 transition-colors",
              isActive
                ? "text-blue-700 dark:text-blue-400"
                : "text-slate-500 group-hover:text-slate-900 dark:text-zinc-400 dark:group-hover:text-zinc-100"
            )}
          />
          {!isCollapsed && <span>{item.label}</span>}
          {isCollapsed && (
            <div
              className={cn(
                "absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 rounded-r-md bg-blue-600 dark:bg-blue-400 transition-opacity",
                isActive ? "opacity-100" : "opacity-0"
              )}
            />
          )}
        </div>
      )}
    </NavLink>
  );
}

export default function DashboardLayout() {
  const { logout, user, isLoading, hasPermission } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { theme, setTheme } = useTheme();
  const spotlight = useSpotlight();
  const [isCollapsed, setIsCollapsed] = useState(() => {
    const stored = localStorage.getItem("sidebarCollapsed");
    return stored === "true";
  });

  const filteredNavItems = useMemo(() => {
    return navItems.filter(item => !item.permission || hasPermission(item.permission));
  }, [user, hasPermission]);

  const filteredSettingsItems = useMemo(() => {
    return settingsItems.filter(item => !item.permission || hasPermission(item.permission));
  }, [user, hasPermission]);

  const breadcrumbItems = useMemo(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const relevant = segments[0] === "dashboard" ? segments.slice(1) : segments;
    const basePath = "/dashboard";

    if (relevant.length === 0) {
      return [{ path: basePath, label: "Overview", isLast: true }];
    }

    return relevant.map((segment, index) => ({
      path: `${basePath}/${relevant.slice(0, index + 1).join("/")}`,
      label: breadcrumbLabels[segment] || segment.replace(/-/g, " "),
      isLast: index === relevant.length - 1,
    }));
  }, [location.pathname]);

  if (!isLoading && user && !user.totp_enabled) {
    return <Navigate to="/auth/setup-2fa" replace />;
  }

  const toggleCollapse = () => {
    const newState = !isCollapsed;
    setIsCollapsed(newState);
    localStorage.setItem("sidebarCollapsed", String(newState));
  };

  const handleLogout = () => {
    logout();
    navigate("/auth/login");
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-white dark:from-black dark:to-black flex font-sans text-foreground">
      {mobileMenuOpen && (
        <button
          type="button"
          aria-label="Close navigation menu"
          className="fixed inset-0 bg-black/80 backdrop-blur-sm z-40 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 bg-slate-100 dark:bg-zinc-900 border-r border-slate-200 dark:border-zinc-800 transform transition-all duration-300 flex flex-col",
          mobileMenuOpen ? "translate-x-0 w-64" : "-translate-x-full lg:translate-x-0",
          isCollapsed ? "lg:w-[70px]" : "lg:w-64"
        )}
      >
        <div
          className={cn(
            "h-14 flex items-center border-b border-slate-100 dark:border-zinc-800 transition-all duration-300",
            isCollapsed ? "justify-center px-0" : "px-4 justify-between"
          )}
        >
          <div className="flex items-center gap-2 overflow-hidden">
            <img src="/logo.svg" alt="InferiaLLM" className="h-14 w-auto shrink-0 object-contain" />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-3 space-y-7 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-zinc-800">
          {hasPermission("deployment:create") && (
            <button
              type="button"
              onClick={() => {
                navigate("/dashboard/deployments/new");
                setMobileMenuOpen(false);
              }}
              className={cn(
                "w-full h-9 inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors",
                isCollapsed && "px-0"
              )}
              aria-label="Create new deployment"
            >
              <Plus className="w-4 h-4" />
              {!isCollapsed && <span>New Deployment</span>}
            </button>
          )}

          <div>
            {!isCollapsed && <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Monitor</div>}
            <nav className="space-y-0.5" aria-label="Primary navigation">
              {filteredNavItems.slice(0, 3).map((item) => (
                <SidebarItem key={item.href} item={item} isCollapsed={isCollapsed} closeMobile={() => setMobileMenuOpen(false)} />
              ))}
            </nav>
          </div>

          <div>
            {!isCollapsed && <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Build</div>}
            <nav className="space-y-0.5" aria-label="Build navigation">
              {filteredNavItems.slice(3).map((item) => (
                <SidebarItem key={item.href} item={item} isCollapsed={isCollapsed} closeMobile={() => setMobileMenuOpen(false)} />
              ))}
            </nav>
          </div>

          <div>
            {!isCollapsed && <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Admin</div>}
            <nav className="space-y-0.5" aria-label="Settings navigation">
              {filteredSettingsItems.map((item) => (
                <SidebarItem key={item.href} item={item} isCollapsed={isCollapsed} closeMobile={() => setMobileMenuOpen(false)} />
              ))}
            </nav>
          </div>
        </div>

        <div className="p-3 border-t border-slate-200 dark:border-zinc-800 bg-slate-100/50 dark:bg-zinc-900/50">
          <div
            className={cn(
              "flex items-center gap-3 px-2 py-2 rounded-lg transition-colors",
              isCollapsed ? "justify-center" : "bg-white/50 dark:bg-black/20 border border-slate-200/50 dark:border-zinc-800/50 shadow-sm"
            )}
          >
            <div className="h-8 w-8 rounded-md bg-blue-600 dark:bg-blue-500 text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-sm">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            {!isCollapsed && (
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-slate-900 dark:text-zinc-100 truncate">{user?.email?.split("@")[0]}</p>
                <p className="text-[10px] text-slate-500 dark:text-zinc-500 truncate uppercase tracking-tighter">Administrator</p>
              </div>
            )}
            {!isCollapsed && (
              <button
                type="button"
                onClick={handleLogout}
                className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 rounded-md transition-all group"
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut className="w-4 h-4 group-hover:scale-110 transition-transform" />
              </button>
            )}
          </div>
          {isCollapsed && (
            <button
              type="button"
              onClick={handleLogout}
              className="mt-2 flex items-center justify-center w-full p-2 text-slate-400 hover:text-red-500 rounded-md transition-colors"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
      </aside>

      <div className={cn("flex-1 flex flex-col min-h-screen transition-all duration-300", isCollapsed ? "lg:ml-[70px]" : "lg:ml-64")}>
        <header className="h-14 border-b border-slate-200 dark:border-zinc-800 bg-white/95 dark:bg-black/95 backdrop-blur sticky top-0 z-30 px-4 sm:px-6 flex items-center justify-between shadow-sm dark:shadow-none">
          <div className="flex items-center gap-4">
            <button
              type="button"
              aria-label="Open navigation menu"
              className="lg:hidden p-2 -ml-2 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded-md text-slate-500"
              onClick={() => setMobileMenuOpen(true)}
            >
              <Menu className="w-5 h-5" />
            </button>

            <button
              type="button"
              className="hidden lg:flex p-2 -ml-2 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded-md text-slate-500 dark:text-zinc-400 transition-colors"
              onClick={toggleCollapse}
              title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {isCollapsed ? <PanelLeftOpen className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
            </button>

            <div className="flex items-center">
              <div className="h-6 w-px bg-slate-200 dark:bg-zinc-800 mx-2 hidden lg:block" />
              <nav className="flex items-center text-sm font-medium" aria-label="Breadcrumb">
                <Link to="/dashboard" className="text-slate-500 dark:text-zinc-500 hover:text-slate-900 dark:hover:text-zinc-100 transition-colors">
                  Dashboard
                </Link>
                {breadcrumbItems.map((item) => (
                  <div key={item.path} className="flex items-center">
                    <ChevronRight className="w-4 h-4 text-slate-400 mx-1" />
                    <Link
                      to={item.path}
                      className={cn(
                        "capitalize transition-colors",
                        item.isLast
                          ? "text-slate-900 dark:text-zinc-100 font-medium pointer-events-none"
                          : "text-slate-500 dark:text-zinc-500 hover:text-slate-900 dark:hover:text-zinc-100 cursor-pointer"
                      )}
                    >
                      {item.label}
                    </Link>
                  </div>
                ))}
              </nav>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="relative p-2 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors text-slate-500 dark:text-zinc-400"
              aria-label="Toggle theme"
            >
              <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <Moon className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
            </button>

            <button
              type="button"
              onClick={spotlight.open}
              className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-slate-50 dark:bg-zinc-950 rounded-md border border-slate-200 dark:border-zinc-800 hover:border-slate-300 dark:hover:border-zinc-700 hover:bg-slate-100 dark:hover:bg-zinc-900 transition-all cursor-pointer"
            >
              <Search className="w-3.5 h-3.5 text-slate-400" />
              <span className="text-sm text-slate-400 w-32 text-left">Search...</span>
              <kbd className="text-[10px] font-mono border rounded px-1 text-slate-400 bg-white dark:bg-zinc-900 dark:border-zinc-700">⌘K</kbd>
            </button>

            <div className="h-8 w-8 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 flex items-center justify-center font-bold text-xs ring-2 ring-white dark:ring-black border border-blue-200 dark:border-blue-800">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
          </div>
        </header>

        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1600px] w-full mx-auto">
          <Outlet />
        </main>

        <SpotlightSearch isOpen={spotlight.isOpen} onClose={spotlight.close} />
      </div>
    </div>
  );
}
