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
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { useTheme } from "@/components/theme-provider";
import { SpotlightSearch, useSpotlight } from "@/components/SpotlightSearch";

export default function DashboardLayout() {
  const { logout, user, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  if (!isLoading && user && !user.totp_enabled) {
    return <Navigate to="/auth/setup-2fa" replace />;
  }
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { theme, setTheme } = useTheme();
  const spotlight = useSpotlight();

  // Collapsible Sidebar State
  const [isCollapsed, setIsCollapsed] = useState(() => {
    const stored = localStorage.getItem("sidebarCollapsed");
    return stored === "true";
  });

  const toggleCollapse = () => {
    const newState = !isCollapsed;
    setIsCollapsed(newState);
    localStorage.setItem("sidebarCollapsed", String(newState));
  };

  const handleLogout = () => {
    logout();
    navigate("/auth/login");
  };

  const navItems = [
    {
      href: "/dashboard",
      label: "Overview",
      icon: LayoutDashboard,
      exact: true,
    },
    { href: "/dashboard/deployments", label: "Deployments", icon: Rocket },
    { href: "/dashboard/compute/instances", label: "Pool", icon: Box },
    { href: "/dashboard/templates", label: "Templates", icon: FileText },
    {
      href: "/dashboard/knowledge-base",
      label: "Knowledge Base",
      icon: Database,
    },
    { href: "/dashboard/api-keys", label: "API Keys", icon: Key },
  ];

  const settingsItems = [
    {
      href: "/dashboard/settings/organization",
      label: "Organization",
      icon: Building2,
    },
    { href: "/dashboard/settings/users", label: "Users", icon: Users },
    { href: "/dashboard/settings/roles", label: "Roles", icon: Shield },
    {
      href: "/dashboard/settings/audit-logs",
      label: "Audit Logs",
      icon: Clock,
    },
    {
      href: "/dashboard/settings/providers",
      label: "Providers",
      icon: Database,
    },
    {
      href: "/dashboard/settings/security",
      label: "Security",
      icon: Shield,
    },
    {
      href: "/dashboard/status",
      label: "System Status",
      icon: Activity,
    },
  ];

  // Breadcrumb logic
  const pathSegments = location.pathname.split("/").filter(Boolean);

  const SidebarItem = ({ item }: { item: { href: string; label: string; icon: any; exact?: boolean } }) => (
    <NavLink
      to={item.href}
      end={item.exact}
      onClick={() => setMobileMenuOpen(false)}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200 group relative",
          isActive
            ? "bg-blue-50 text-blue-600 dark:bg-zinc-800 dark:text-blue-400"
            : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100",
          isCollapsed && "justify-center px-2"
        )
      }
      title={isCollapsed ? item.label : undefined}
    >
      <item.icon
        className={cn(
          "w-4 h-4 shrink-0 transition-colors",
          ({ isActive }: { isActive: boolean }) =>
            isActive
              ? "text-blue-600 dark:text-blue-400"
              : "text-slate-500 group-hover:text-slate-900 dark:text-zinc-400 dark:group-hover:text-zinc-100"
        )}
      />
      {!isCollapsed && <span>{item.label}</span>}

      {/* Active Indicator Line for Collapsed Mode (Optional Polish) */}
      {isCollapsed && (
        <div className={cn(
          "absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 rounded-r-md bg-blue-600 dark:bg-blue-400 transition-opacity",
          location.pathname === item.href ? "opacity-100" : "opacity-0"
        )} />
      )}
    </NavLink>
  );

  return (
    <div className="min-h-screen bg-slate-50/50 dark:bg-black flex font-sans text-foreground">
      {/* Mobile Overlay */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 bg-black/80 backdrop-blur-sm z-40 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 bg-slate-100 dark:bg-zinc-900 border-r border-slate-200 dark:border-zinc-800 transform transition-all duration-300 flex flex-col",
          mobileMenuOpen ? "translate-x-0 w-64" : "-translate-x-full lg:translate-x-0",
          // Desktop Width Logic
          isCollapsed ? "lg:w-[70px]" : "lg:w-64"
        )}
      >
        <div className={cn(
          "h-14 flex items-center border-b border-slate-100 dark:border-zinc-800 transition-all duration-300",
          isCollapsed ? "justify-center px-0" : "px-4 justify-between"
        )}>
          <div className="flex items-center gap-2 overflow-hidden">
            <img src="/logo.svg" alt="InferiaLLM" className="h-12.5 w-auto shrink-0 object-contain" />

          </div>
        </div>



        <div className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-3 space-y-7 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-zinc-800">
          <div>
            {!isCollapsed && (
              <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider animate-in fade-in">
                Core
              </div>
            )}
            <nav className="space-y-0.5">
              {navItems.slice(0, 3).map((item) => (
                <SidebarItem key={item.href} item={item} />
              ))}
            </nav>
          </div>

          <div>
            {!isCollapsed && (
              <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider animate-in fade-in">
                Platform
              </div>
            )}
            <nav className="space-y-0.5">
              {navItems.slice(3).map((item) => (
                <SidebarItem key={item.href} item={item} />
              ))}
            </nav>
          </div>

          <div>
            {!isCollapsed && (
              <div className="px-3 mb-2 text-[10px] font-bold text-muted-foreground uppercase tracking-wider animate-in fade-in">
                Settings
              </div>
            )}
            <nav className="space-y-0.5">
              {settingsItems.map((item) => (
                <SidebarItem key={item.href} item={item} />
              ))}
            </nav>
          </div>
        </div>

        <div className="p-3 border-t border-slate-200 dark:border-zinc-800 bg-slate-100/50 dark:bg-zinc-900/50">
          <div className={cn(
            "flex items-center gap-3 px-2 py-2 rounded-lg transition-colors",
            isCollapsed ? "justify-center" : "bg-white/50 dark:bg-black/20 border border-slate-200/50 dark:border-zinc-800/50 shadow-sm"
          )}>
            <div className="h-8 w-8 rounded-md bg-blue-600 dark:bg-blue-500 text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-sm">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
            {!isCollapsed && (
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-slate-900 dark:text-zinc-100 truncate">
                  {user?.email?.split('@')[0]}
                </p>
                <p className="text-[10px] text-slate-500 dark:text-zinc-500 truncate uppercase tracking-tighter">
                  Administrator
                </p>
              </div>
            )}
            {!isCollapsed && (
              <button
                onClick={handleLogout}
                className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 rounded-md transition-all group"
                title="Sign Out"
              >
                <LogOut className="w-4 h-4 group-hover:scale-110 transition-transform" />
              </button>
            )}
          </div>
          {isCollapsed && (
            <button
              onClick={handleLogout}
              className="mt-2 flex items-center justify-center w-full p-2 text-slate-400 hover:text-red-500 rounded-md transition-colors"
              title="Sign Out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
      </aside>

      {/* Main Content */}
      <div className={cn(
        "flex-1 flex flex-col min-h-screen transition-all duration-300",
        // Only apply left margin on desktop, mobile uses overlay
        isCollapsed ? "lg:ml-[70px]" : "lg:ml-64"
      )}>
        {/* Header */}
        <header className="h-14 border-b border-slate-200 dark:border-zinc-800 bg-white dark:bg-black sticky top-0 z-30 px-4 sm:px-6 flex items-center justify-between shadow-sm dark:shadow-none">
          <div className="flex items-center gap-4">
            {/* Mobile Menu Toggle */}
            <button
              className="lg:hidden p-2 -ml-2 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded-md text-slate-500"
              onClick={() => setMobileMenuOpen(true)}
            >
              <Menu className="w-5 h-5" />
            </button>

            {/* Desktop Collapse Toggle */}
            <button
              className="hidden lg:flex p-2 -ml-2 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded-md text-slate-500 dark:text-zinc-400 transition-colors"
              onClick={toggleCollapse}
              title={isCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
            >
              {isCollapsed ? <PanelLeftOpen className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
            </button>

            <div className="flex items-center">
              <div className="h-6 w-px bg-slate-200 dark:bg-zinc-800 mx-2 hidden lg:block" />
              <nav className="flex items-center text-sm font-medium">
                {pathSegments.map((segment, index) => {
                  const path = `/${pathSegments.slice(0, index + 1).join("/")}`;
                  const isLast = index === pathSegments.length - 1;

                  return (
                    <div key={index} className="flex items-center">
                      {index > 0 && (
                        <ChevronRight className="w-4 h-4 text-slate-400 mx-1" />
                      )}
                      <Link
                        to={path}
                        className={cn(
                          "capitalize transition-colors",
                          isLast
                            ? "text-slate-900 dark:text-zinc-100 font-medium pointer-events-none"
                            : "text-slate-500 dark:text-zinc-500 hover:text-slate-900 dark:hover:text-zinc-100 cursor-pointer"
                        )}
                      >
                        {segment.replace(/-/g, " ")}
                      </Link>
                    </div>
                  );
                })}
              </nav>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="relative p-2 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors text-slate-500 dark:text-zinc-400"
            >
              <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <Moon className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
              <span className="sr-only">Toggle theme</span>
            </button>

            <button
              onClick={spotlight.open}
              className="hidden md:flex items-center gap-2 px-3 py-1.5 bg-slate-50 dark:bg-zinc-950 rounded-md border border-slate-200 dark:border-zinc-800 hover:border-slate-300 dark:hover:border-zinc-700 hover:bg-slate-100 dark:hover:bg-zinc-900 transition-all cursor-pointer"
            >
              <Search className="w-3.5 h-3.5 text-slate-400" />
              <span className="text-sm text-slate-400 w-32 text-left">Search...</span>
              <div className="flex items-center gap-1">
                <kbd className="text-[10px] font-mono border rounded px-1 text-slate-400 bg-white dark:bg-zinc-900 dark:border-zinc-700">
                  ⌘K
                </kbd>
              </div>
            </button>

            <div className="h-8 w-8 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 flex items-center justify-center font-bold text-xs ring-2 ring-white dark:ring-black border border-blue-200 dark:border-blue-800">
              {user?.email?.charAt(0).toUpperCase()}
            </div>
          </div>
        </header>

        <main className="flex-1 p-6 transition-all">
          <Outlet />
        </main>

        {/* Spotlight Search Modal */}
        <SpotlightSearch isOpen={spotlight.isOpen} onClose={spotlight.close} />
      </div>
    </div>
  );
}
