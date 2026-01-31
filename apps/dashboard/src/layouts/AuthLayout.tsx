
import { Outlet } from "react-router-dom"
import { Sun, Moon } from "lucide-react"
import { useTheme } from "@/components/theme-provider"

export default function AuthLayout() {
    const { theme, setTheme } = useTheme()

    return (
        <div className="min-h-screen bg-background text-foreground flex flex-col">
            {/* Minimal Auth Navbar */}
            <header className="h-14 border-b border-slate-200 dark:border-zinc-800 bg-white dark:bg-black px-4 sm:px-6 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <img src="/logo.svg" alt="InferiaLLM" className="h-15 w-auto shrink-0 object-contain" />

                </div>

                <button
                    onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                    className="relative p-2 rounded-md hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors text-slate-500 dark:text-zinc-400"
                    aria-label="Toggle theme"
                >
                    <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                    <Moon className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                </button>
            </header>

            {/* Centered Content */}
            <div className="flex-1 flex items-center justify-center">
                <Outlet />
            </div>
        </div>
    )
}
