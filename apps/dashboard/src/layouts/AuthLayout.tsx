
import { Outlet } from "react-router-dom"
import { Sun, Moon } from "lucide-react"
import { useTheme } from "@/components/theme-provider"

export default function AuthLayout() {
    const { theme, setTheme } = useTheme()

    return (
        <div className="min-h-screen bg-background text-foreground flex flex-col">
            <header className="w-full pt-4 sm:pt-5">
                <div className="mx-auto w-full max-w-5xl px-4 sm:px-8">
                    <div className="h-14 rounded-2xl border border-border/70 bg-card/70 px-4 sm:px-5 backdrop-blur-md shadow-sm shadow-black/5 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <img src="/logo.svg" alt="InferiaLLM" className="h-10 w-auto shrink-0 object-contain" />
                        </div>

                        <button
                            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                            className="relative rounded-xl border border-border/70 bg-background/60 p-2 text-slate-500 transition-colors hover:bg-accent hover:text-foreground dark:text-zinc-400"
                            aria-label="Toggle theme"
                        >
                            <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                            <Moon className="absolute top-1/2 left-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                        </button>
                    </div>
                </div>
            </header>

            <div className="flex-1 flex items-center justify-center pb-8 pt-4">
                <Outlet />
            </div>
        </div>
    )
}
