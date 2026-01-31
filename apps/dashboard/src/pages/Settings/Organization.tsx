import { useState, useEffect } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Scale, Save, Activity, Shield, RefreshCw } from "lucide-react"
import { LoadingScreen } from "@/components/ui/LoadingScreen"

interface ConfigResponse {
    policy_type: string
    config_json: any
}

interface OrganizationData {
    id: string
    name: string
    log_payloads: boolean
}

interface UsageStat {
    key_name: string
    key_prefix: string
    requests: number
    tokens: number
}

export default function Organization() {
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)
    const [jsonInput, setJsonInput] = useState("{}")
    const [usageStats, setUsageStats] = useState<UsageStat[]>([])
    const [orgData, setOrgData] = useState<OrganizationData | null>(null)

    const fetchOrgData = async () => {
        try {
            const { data } = await api.get<OrganizationData>("/management/organizations/me")
            setOrgData(data)
        } catch (error) {
            console.error("Failed to fetch organization data:", error)
        }
    }

    const fetchUsageStats = async () => {
        try {
            const { data } = await api.get<UsageStat[]>("/management/config/quota/usage")
            setUsageStats(data)
        } catch (error) {
            console.error("Failed to fetch usage stats:", error)
        }
    }

    // Fetch Quota Config
    const fetchConfig = async () => {
        setIsLoading(true)
        try {
            // Get current org ID first (from auth/me or organizations endpoint)
            // For now, let's assume the user is editing their current organization.
            // We can fetch the organization-wide policy.
            const { data } = await api.get<ConfigResponse>(`/management/config/quota`)
            setJsonInput(JSON.stringify(data.config_json || { request_limit: 1000, token_limit: 100000 }, null, 2))
        } catch (error) {
            console.error("Failed to fetch quota config:", error)
            setJsonInput(JSON.stringify({ request_limit: 1000, token_limit: 100000 }, null, 2))
        } finally {
            setIsLoading(false)
        }
    }

    const handleUpdateLogPayloads = async (enabled: boolean) => {
        if (!orgData) return
        try {
            // Optimistic update
            setOrgData({ ...orgData, log_payloads: enabled })
            
            await api.patch("/management/organizations/me", {
                log_payloads: enabled
            })
            toast.success(`Inference payload logging ${enabled ? "enabled" : "disabled"}`)
        } catch (error) {
            console.error(error)
            toast.error("Failed to update logging preference")
            // Rollback
            fetchOrgData()
        }
    }

    const handleSave = async () => {
        try {
            const parsed = JSON.parse(jsonInput)
            setIsSaving(true)
            await api.post("/management/config", {
                policy_type: "quota",
                config_json: parsed
            })
            toast.success("Organization quota updated successfully")
            // We don't need to re-fetch config as we just updated it locally via jsonInput, 
            // but fetching usage stats might be good if we had real-time updates.
        } catch (error) {
            console.error(error)
            toast.error("Failed to update quota")
        } finally {
            setIsSaving(false)
        }
    }

    useEffect(() => {
        // Initial load
        const init = async () => {
            setIsLoading(true)
            await Promise.all([fetchConfig(), fetchUsageStats(), fetchOrgData()])
            setIsLoading(false)
        }
        init()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    if (isLoading) return <LoadingScreen message="Loading organization settings..." />

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <div className="flex items-center text-sm text-muted-foreground mb-2">
                        <span>Settings</span>
                        <span className="mx-2">/</span>
                        <span className="text-foreground font-medium">Organization</span>
                    </div>
                    <h1 className="text-3xl font-bold tracking-tight mb-2">Organization Settings</h1>
                    <p className="text-muted-foreground">Manage your organization-wide policies and preferences.</p>
                </div>
            </div>

            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center justify-between mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider flex items-center gap-2">
                        <Scale className="w-4 h-4" /> Quota Management
                    </h3>
                    <button
                        onClick={handleSave}
                        disabled={isSaving}
                        className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                        <Save className="w-4 h-4" />
                        {isSaving ? "Saving..." : "Save Changes"}
                    </button>
                </div>

                <div className="space-y-6">
                    <div className="p-4 bg-muted/30 border rounded-lg text-sm text-foreground">
                        Define usage limits for all users and deployments within this organization.
                    </div>

                    <div className="grid gap-6 md:grid-cols-2">
                        <div className="space-y-3">
                            <label className="text-sm font-medium">Daily Request Limit</label>
                            <input
                                type="number"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                min="1"
                                value={JSON.parse(jsonInput).request_limit || 1000}
                                onChange={(e) => {
                                    const current = JSON.parse(jsonInput)
                                    current.request_limit = parseInt(e.target.value)
                                    setJsonInput(JSON.stringify(current, null, 2))
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Maximum number of inference requests per day across the organization.</p>
                        </div>

                        <div className="space-y-3">
                            <label className="text-sm font-medium">Daily Token Limit</label>
                            <input
                                type="number"
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                min="1"
                                value={JSON.parse(jsonInput).token_limit || 100000}
                                onChange={(e) => {
                                    const current = JSON.parse(jsonInput)
                                    current.token_limit = parseInt(e.target.value)
                                    setJsonInput(JSON.stringify(current, null, 2))
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Maximum total tokens (prompt + completion) per day.</p>
                        </div>
                    </div>
                </div>
            </div>

            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center gap-2 mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider flex items-center gap-2">
                        <Shield className="w-4 h-4" /> Privacy & Data
                    </h3>
                </div>

                <div className="space-y-6">
                    <div className="flex items-center justify-between p-4 bg-muted/30 border rounded-lg">
                        <div className="space-y-0.5">
                            <div className="text-sm font-medium">Log Inference Payloads</div>
                            <div className="text-xs text-muted-foreground">
                                If enabled, full prompt and response content will be stored in the inference logs.
                                Disable this to only record metadata and performance metrics.
                            </div>
                        </div>
                        <button
                            onClick={() => handleUpdateLogPayloads(!orgData?.log_payloads)}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
                                orgData?.log_payloads ? "bg-primary" : "bg-muted"
                            }`}
                        >
                            <span
                                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                    orgData?.log_payloads ? "translate-x-6" : "translate-x-1"
                                }`}
                            />
                        </button>
                    </div>
                </div>
            </div>

            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center gap-2 mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider flex items-center gap-2">
                        <Activity className="w-4 h-4" /> Usage Statistics (Today)
                    </h3>
                </div>

                <div className="rounded-md border">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-muted/50 text-muted-foreground font-medium">
                            <tr className="border-b">
                                <th className="px-4 py-3 font-medium">Key Name</th>
                                <th className="px-4 py-3 font-medium">Prefix</th>
                                <th className="px-4 py-3 font-medium">Requests</th>
                                <th className="px-4 py-3 font-medium">Tokens</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {usageStats.map((stat, i) => (
                                <tr key={i} className="hover:bg-muted/50 transition-colors">
                                    <td className="px-4 py-3 font-medium">{stat.key_name}</td>
                                    <td className="px-4 py-3 font-mono text-xs">{stat.key_prefix}</td>
                                    <td className="px-4 py-3">{stat.requests}</td>
                                    <td className="px-4 py-3">{stat.tokens.toLocaleString()}</td>
                                </tr>
                            ))}
                            {usageStats.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                                        No usage recorded today.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}
