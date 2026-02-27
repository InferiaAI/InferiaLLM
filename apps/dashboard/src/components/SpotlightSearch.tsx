import { useState, useEffect, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Search, LayoutDashboard, Rocket, Box, FileText, Database, Key, Building2, Users, Shield, Clock, Activity, X, Cloud, BarChart3 } from "lucide-react"
import { cn } from "@/lib/utils"

interface SearchItem {
    title: string
    path: string
    icon: any
    category: "Navigation" | "Settings" | "Actions"
    keywords?: string[]
}

const SEARCH_ITEMS: SearchItem[] = [
    // Navigation
    { title: "Overview", path: "/dashboard", icon: LayoutDashboard, category: "Navigation", keywords: ["home", "dashboard", "main"] },
    { title: "Insights", path: "/dashboard/insights", icon: BarChart3, category: "Navigation", keywords: ["analytics", "metrics", "latency", "tokens", "charts"] },
    { title: "Deployments", path: "/dashboard/deployments", icon: Rocket, category: "Navigation", keywords: ["deploy", "models", "inference"] },
    { title: "New Deployment", path: "/dashboard/deployments/new", icon: Rocket, category: "Actions", keywords: ["create", "add", "deploy"] },
    { title: "Compute Pools", path: "/dashboard/compute/pools", icon: Box, category: "Navigation", keywords: ["pool", "instances", "compute", "gpu"] },
    { title: "New Pool", path: "/dashboard/compute/pools/new", icon: Box, category: "Actions", keywords: ["create", "add", "pool"] },
    { title: "Templates", path: "/dashboard/templates", icon: FileText, category: "Navigation", keywords: ["prompt", "template"] },
    { title: "Knowledge Base", path: "/dashboard/knowledge-base", icon: Database, category: "Navigation", keywords: ["rag", "documents", "knowledge"] },
    { title: "API Keys", path: "/dashboard/api-keys", icon: Key, category: "Navigation", keywords: ["keys", "tokens", "auth"] },
    // Settings
    { title: "Organization", path: "/dashboard/settings/organization", icon: Building2, category: "Settings", keywords: ["org", "company"] },
    { title: "Users", path: "/dashboard/settings/users", icon: Users, category: "Settings", keywords: ["team", "members", "invite"] },
    { title: "Roles", path: "/dashboard/settings/roles", icon: Shield, category: "Settings", keywords: ["permissions", "access"] },
    { title: "Audit Logs", path: "/dashboard/settings/audit-logs", icon: Clock, category: "Settings", keywords: ["logs", "history", "activity"] },
    { title: "Infrastructure & Compute", path: "/dashboard/settings/providers/cloud", icon: Cloud, category: "Settings", keywords: ["infrastructure", "aws", "nosana", "akash", "depin", "cloud"] },
    { title: "Vector Databases", path: "/dashboard/settings/providers/vector-db", icon: Database, category: "Settings", keywords: ["chroma", "pinecone", "database", "embeddings", "rag"] },
    { title: "Security Guardrails", path: "/dashboard/settings/providers/guardrails", icon: Shield, category: "Settings", keywords: ["pii", "groq", "lakera", "firewall", "security", "guard"] },
    { title: "AWS Configuration", path: "/dashboard/settings/providers/cloud/aws", icon: Cloud, category: "Settings", keywords: ["amazon", "s3", "iam", "cloud"] },
    { title: "ChromaDB Configuration", path: "/dashboard/settings/providers/vector-db/chroma", icon: Database, category: "Settings", keywords: ["chroma", "vector", "local"] },
    { title: "Security", path: "/dashboard/settings/security", icon: Shield, category: "Settings", keywords: ["2fa", "auth", "password", "security", "totp"] },
    { title: "System Status", path: "/dashboard/status", icon: Activity, category: "Settings", keywords: ["health", "services", "status", "uptime"] },
]

interface SpotlightSearchProps {
    isOpen: boolean
    onClose: () => void
}

export function SpotlightSearch({ isOpen, onClose }: SpotlightSearchProps) {
    const navigate = useNavigate()
    const [query, setQuery] = useState("")
    const [selectedIndex, setSelectedIndex] = useState(0)

    const filteredItems = query
        ? SEARCH_ITEMS.filter(item => {
            const searchLower = query.toLowerCase()
            return (
                item.title.toLowerCase().includes(searchLower) ||
                item.path.toLowerCase().includes(searchLower) ||
                item.keywords?.some(k => k.toLowerCase().includes(searchLower))
            )
        })
        : SEARCH_ITEMS

    // Group by category
    const groupedItems = filteredItems.reduce((acc, item) => {
        if (!acc[item.category]) acc[item.category] = []
        acc[item.category].push(item)
        return acc
    }, {} as Record<string, SearchItem[]>)

    const flatItems = Object.values(groupedItems).flat()

    const handleSelect = useCallback((item: SearchItem) => {
        navigate(item.path)
        onClose()
        setQuery("")
    }, [navigate, onClose])

    // Keyboard navigation
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (!isOpen) return

            switch (e.key) {
                case "ArrowDown":
                    e.preventDefault()
                    setSelectedIndex(prev => Math.min(prev + 1, flatItems.length - 1))
                    break
                case "ArrowUp":
                    e.preventDefault()
                    setSelectedIndex(prev => Math.max(prev - 1, 0))
                    break
                case "Enter":
                    e.preventDefault()
                    if (flatItems[selectedIndex]) {
                        handleSelect(flatItems[selectedIndex])
                    }
                    break
                case "Escape":
                    e.preventDefault()
                    onClose()
                    break
            }
        }

        window.addEventListener("keydown", handleKeyDown)
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [isOpen, flatItems, selectedIndex, handleSelect, onClose])

    // Reset on open
    useEffect(() => {
        if (isOpen) {
            setQuery("")
            setSelectedIndex(0)
        }
    }, [isOpen])

    if (!isOpen) return null

    let currentIndex = 0

    return (
        <>
            {/* Backdrop */}
            <div
                className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50"
                onClick={onClose}
            />

            {/* Modal */}
            <div className="fixed top-[20%] left-1/2 -translate-x-1/2 w-full max-w-xl z-50 animate-in fade-in zoom-in-95 duration-200">
                <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-2xl border dark:border-zinc-800 overflow-hidden">
                    {/* Search Input */}
                    <div className="flex items-center px-4 border-b dark:border-zinc-800">
                        <Search className="w-5 h-5 text-slate-400 shrink-0" />
                        <input
                            autoFocus
                            value={query}
                            onChange={(e) => {
                                setQuery(e.target.value)
                                setSelectedIndex(0)
                            }}
                            placeholder="Search pages and actions..."
                            className="flex-1 h-14 px-4 bg-transparent border-none outline-none text-base text-slate-900 dark:text-zinc-100 placeholder:text-slate-400"
                        />
                        <button
                            onClick={onClose}
                            className="p-1.5 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded-md transition-colors"
                        >
                            <X className="w-4 h-4 text-slate-400" />
                        </button>
                    </div>

                    {/* Results */}
                    <div className="max-h-[400px] overflow-y-auto">
                        {Object.entries(groupedItems).map(([category, items]) => (
                            <div key={category}>
                                <div className="px-4 py-2 text-xs font-semibold text-slate-500 dark:text-zinc-500 uppercase tracking-wider bg-slate-50 dark:bg-zinc-950">
                                    {category}
                                </div>
                                {items.map((item) => {
                                    const itemIndex = currentIndex++
                                    const isSelected = itemIndex === selectedIndex

                                    return (
                                        <button
                                            key={item.path}
                                            onClick={() => handleSelect(item)}
                                            onMouseEnter={() => setSelectedIndex(itemIndex)}
                                            className={cn(
                                                "w-full flex items-center gap-3 px-4 py-3 text-left transition-colors",
                                                isSelected
                                                    ? "bg-emerald-50 dark:bg-emerald-900/20"
                                                    : "hover:bg-slate-50 dark:hover:bg-zinc-800/50"
                                            )}
                                        >
                                            <div className={cn(
                                                "p-2 rounded-lg",
                                                isSelected
                                                    ? "bg-emerald-100 dark:bg-emerald-900/40"
                                                    : "bg-slate-100 dark:bg-zinc-800"
                                            )}>
                                                <item.icon className={cn(
                                                    "w-4 h-4",
                                                    isSelected
                                                        ? "text-emerald-600 dark:text-emerald-400"
                                                        : "text-slate-500 dark:text-zinc-400"
                                                )} />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className={cn(
                                                    "font-medium text-sm",
                                                    isSelected
                                                        ? "text-emerald-700 dark:text-emerald-300"
                                                        : "text-slate-900 dark:text-zinc-100"
                                                )}>
                                                    {item.title}
                                                </div>
                                                <div className="text-xs text-slate-500 dark:text-zinc-500 truncate">
                                                    {item.path}
                                                </div>
                                            </div>
                                            {isSelected && (
                                                <kbd className="hidden sm:flex items-center gap-1 px-2 py-1 text-xs bg-slate-100 dark:bg-zinc-800 text-slate-500 dark:text-zinc-400 rounded border dark:border-zinc-700">
                                                    Enter ↵
                                                </kbd>
                                            )}
                                        </button>
                                    )
                                })}
                            </div>
                        ))}

                        {filteredItems.length === 0 && (
                            <div className="p-8 text-center text-slate-500 dark:text-zinc-500">
                                <Search className="w-8 h-8 mx-auto mb-2 opacity-30" />
                                <p>No results found for "{query}"</p>
                            </div>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="px-4 py-2 border-t dark:border-zinc-800 bg-slate-50 dark:bg-zinc-950 flex items-center justify-between text-xs text-slate-500 dark:text-zinc-500">
                        <div className="flex items-center gap-4">
                            <span className="flex items-center gap-1">
                                <kbd className="px-1.5 py-0.5 bg-white dark:bg-zinc-800 border dark:border-zinc-700 rounded text-[10px]">↑</kbd>
                                <kbd className="px-1.5 py-0.5 bg-white dark:bg-zinc-800 border dark:border-zinc-700 rounded text-[10px]">↓</kbd>
                                Navigate
                            </span>
                            <span className="flex items-center gap-1">
                                <kbd className="px-1.5 py-0.5 bg-white dark:bg-zinc-800 border dark:border-zinc-700 rounded text-[10px]">↵</kbd>
                                Select
                            </span>
                            <span className="flex items-center gap-1">
                                <kbd className="px-1.5 py-0.5 bg-white dark:bg-zinc-800 border dark:border-zinc-700 rounded text-[10px]">esc</kbd>
                                Close
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

// Hook for opening spotlight with ⌘K
export function useSpotlight() {
    const [isOpen, setIsOpen] = useState(false)

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "k") {
                e.preventDefault()
                setIsOpen(prev => !prev)
            }
        }

        window.addEventListener("keydown", handleKeyDown)
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [])

    return {
        isOpen,
        open: () => setIsOpen(true),
        close: () => setIsOpen(false),
    }
}
