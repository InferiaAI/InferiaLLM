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
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";
import { useTheme } from "@/components/theme-provider";
import { SpotlightSearch, useSpotlight } from "@/components/SpotlightSearch";

const navItems: (NavItem & { permission?: string })[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, exact: true, permission: "organization:view" },
  { href: "/dashboard/insights", label: "Insights", icon: BarChart3, permission: "deployment:list" },
  { href: "/dashboard/deployments", label: "Deployments", icon: Rocket, permission: "deployment:list" },
  { href: "/dashboard/compute/pools", label: "Compute Pools", icon: Box, permission: "deployment:list" },
  { href: "/dashboard/templates", label: "Templates", icon: FileText, permission: "prompt_template:list" },
  { href: "/dashboard/knowledge-base", label: "Knowledge Base", icon: Database, permission: "knowledge_base:list" },
  { href: "/dashboard/api-keys", label: "API Keys", icon: Key, permission: "api_key:list" },
];

const settingsItems: (NavItem & { permission?: string })[] = [
  { href: "/dashboard/settings/organization", label: "Organization", icon: Building2, permission: "organization:view" },
  { href: "/dashboard/settings/users", label: "Users", icon: Users, permission: "member:list" },
  { href: "/dashboard/settings/roles", label: "Roles", icon: Shield, permission: "role:list" },
  { href: "/dashboard/settings/audit-logs", label: "Audit Logs", icon: Clock, permission: "audit_log:list" },
  { href: "/dashboard/settings/providers", label: "Providers", icon: Database, permission: "organization:update" },
  { href: "/dashboard/settings/security", label: "Security", icon: Shield },
  { href: "/dashboard/status", label: "System Status", icon: Activity, permission: "organization:view" },
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
    <NavLink
      to={item.href}
      end={item.exact}
      onClick={closeMobile}
      title={isCollapsed ? item.label : undefined}
      aria-label={item.label}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
          isActive
            ? "bg-emerald-500/12 text-emerald-700 dark:bg-primary/15 dark:text-primary"
            : "text-slate-600 hover:bg-slate-900/[0.03] hover:text-slate-900 dark:text-muted-foreground dark:hover:bg-white/5 dark:hover:text-foreground",
          isCollapsed && "justify-center px-2.5"
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            className={cn(
              "absolute left-0 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r-md bg-emerald-600 transition-opacity dark:bg-primary",
              isActive ? "opacity-100" : "opacity-0"
            )}
            aria-hidden="true"
          />
          <Icon
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              isActive
                ? "text-emerald-700 dark:text-primary"
                : "text-slate-500 group-hover:text-slate-900 dark:text-muted-foreground dark:group-hover:text-foreground"
            )}
          />
          <span className={cn("truncate", isCollapsed && "sr-only")}>{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

function NavSection({
  title,
  items,
  isCollapsed,
  closeMobile,
  label,
}: {
  title: string;
  items: NavItem[];
  isCollapsed: boolean;
  closeMobile: () => void;
  label: string;
}) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div>
      {!isCollapsed && (
        <div className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">{title}</div>
      )}
      <nav className="space-y-0.5" aria-label={label}>
        {items.map((item) => (
          <SidebarItem key={item.href} item={item} isCollapsed={isCollapsed} closeMobile={closeMobile} />
        ))}
      </nav>
    </div>
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

  const primaryRole = (user?.roles?.[0] || "member")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const filteredNavItems = useMemo(
    () => navItems.filter((item) => !item.permission || hasPermission(item.permission)),
    [hasPermission]
  );

  const filteredSettingsItems = useMemo(
    () => settingsItems.filter((item) => !item.permission || hasPermission(item.permission)),
    [hasPermission]
  );

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
    const nextState = !isCollapsed;
    setIsCollapsed(nextState);
    localStorage.setItem("sidebarCollapsed", String(nextState));
  };

  const closeMobile = () => setMobileMenuOpen(false);

  const handleLogout = () => {
    logout();
    navigate("/auth/login");
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      {mobileMenuOpen && (
        <button
          type="button"
          aria-label="Close navigation menu"
          className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm lg:hidden"
          onClick={closeMobile}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-3 left-3 z-50 flex w-72 flex-col overflow-hidden rounded-2xl border border-border/70 bg-card shadow-lg shadow-black/5 transition-all duration-300",
          mobileMenuOpen ? "translate-x-0" : "-translate-x-[110%] lg:translate-x-0",
          isCollapsed ? "lg:w-[84px]" : "lg:w-72"
        )}
      >
        <div
          className={cn(
            "flex h-16 items-center border-b border-border/70 transition-all duration-300",
            isCollapsed ? "justify-center px-2" : "justify-between px-4"
          )}
        >
          <img src="/logo.svg" alt="InferiaLLM" className="h-10 w-auto shrink-0 object-contain" />
        </div>

        <div className="flex-1 space-y-7 overflow-y-auto overflow-x-hidden px-3 py-4 scrollbar-thin scrollbar-thumb-slate-300/70 dark:scrollbar-thumb-muted-foreground/25">
          {hasPermission("deployment:create") && (
            <button
              type="button"
              onClick={() => {
                navigate("/dashboard/deployments/new");
                closeMobile();
              }}
              className={cn(
                "inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-semibold text-primary-foreground shadow-lg shadow-primary/25 transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                isCollapsed && "px-0"
              )}
              aria-label="Create new deployment"
            >
              <Plus className="h-4 w-4" />
              {!isCollapsed && <span>New Deployment</span>}
            </button>
          )}

          <NavSection
            title="Monitor"
            label="Primary navigation"
            items={filteredNavItems.slice(0, 3)}
            isCollapsed={isCollapsed}
            closeMobile={closeMobile}
          />

          <NavSection
            title="Build"
            label="Build navigation"
            items={filteredNavItems.slice(3)}
            isCollapsed={isCollapsed}
            closeMobile={closeMobile}
          />

          <NavSection
            title="Admin"
            label="Settings navigation"
            items={filteredSettingsItems}
            isCollapsed={isCollapsed}
            closeMobile={closeMobile}
          />
        </div>

        <div className="border-t border-border/70 bg-background/50 p-3">
          <div
            className={cn(
              "flex items-center gap-3 rounded-xl border border-border/70 bg-card/70 px-2 py-2",
              isCollapsed && "justify-center"
            )}
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            {!isCollapsed && (
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-semibold text-foreground">{user?.email?.split("@")[0]}</p>
                <p className="truncate text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{primaryRole}</p>
              </div>
            )}
            {!isCollapsed && (
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-md p-1.5 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/30"
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </button>
            )}
          </div>
          {isCollapsed && (
            <button
              type="button"
              onClick={handleLogout}
              className="mt-2 flex w-full items-center justify-center rounded-md p-2 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/30"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          )}
        </div>
      </aside>

      <div
        className={cn(
          "flex min-h-screen flex-1 flex-col transition-all duration-300",
          isCollapsed ? "lg:ml-[108px]" : "lg:ml-[312px]"
        )}
      >
        <header className="sticky top-3 z-30 mx-3 mt-3 flex h-14 items-center justify-between rounded-2xl border border-border/70 bg-card px-4 shadow-sm sm:px-6">
          <div className="flex items-center gap-2 sm:gap-4">
            <button
              type="button"
              aria-label="Open navigation menu"
              className="rounded-lg p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 lg:hidden"
              onClick={() => setMobileMenuOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </button>

            <button
              type="button"
              className="hidden rounded-lg p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 lg:flex"
              onClick={toggleCollapse}
              title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {isCollapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
            </button>

            <div className="hidden h-6 w-px bg-border sm:block" />

            <nav className="hidden items-center text-sm font-medium sm:flex" aria-label="Breadcrumb">
              <Link
                to="/dashboard"
                className="text-muted-foreground transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                Dashboard
              </Link>
              {breadcrumbItems.map((item) => (
                <div key={item.path} className="flex items-center">
                  <ChevronRight className="mx-1 h-4 w-4 text-muted-foreground/60" />
                  <Link
                    to={item.path}
                    className={cn(
                      "capitalize transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                      item.isLast
                        ? "pointer-events-none font-semibold text-foreground"
                        : "cursor-pointer text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {item.label}
                  </Link>
                </div>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <button
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="relative rounded-lg border border-border/70 bg-background/60 p-2 text-muted-foreground transition hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              aria-label="Toggle theme"
            >
              <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <Moon className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
            </button>

            <button
              type="button"
              onClick={spotlight.open}
              aria-label="Open search"
              className="hidden items-center gap-2 rounded-lg border border-border/70 bg-background/60 px-3 py-1.5 transition hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 md:flex"
            >
              <Search className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="w-28 text-left text-sm text-muted-foreground">Search...</span>
              <kbd className="rounded border border-border bg-card px-1 text-[10px] font-mono text-muted-foreground">⌘K</kbd>
            </button>
          </div>
        </header>

        <main className="flex-1 px-3 pb-6 pt-4 sm:px-4 lg:px-6">
          <div className="mx-auto w-full max-w-[1600px]">
            <Outlet />
          </div>
        </main>

        <SpotlightSearch isOpen={spotlight.isOpen} onClose={spotlight.close} />
      </div>
    </div>
  );
}
