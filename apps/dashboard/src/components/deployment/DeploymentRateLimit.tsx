import { useEffect, useState } from "react"
import { Save } from "lucide-react"
import { toast } from "sonner"
import api from "@/lib/api"

interface DeploymentRateLimitProps {
    deploymentId: string
}

interface ConfigResponse {
    policy_type: string
    config_json: Record<string, any>
    updated_at?: string
}

const DEFAULT_CONFIG = { enabled: true, rpm: 60 }

export default function DeploymentRateLimit({ deploymentId }: DeploymentRateLimitProps) {
    const [loading, setLoading] = useState(false)
    const [jsonInput, setJsonInput] = useState("{}")
    const [dbConfig, setDbConfig] = useState<ConfigResponse | null>(null)

    const fetchConfig = async () => {
        setLoading(true)
        try {
            const { data } = await api.get(`/management/config/rate_limit?deployment_id=${deploymentId}`)

            const config = (data.config_json && Object.keys(data.config_json).length > 0)
                ? data.config_json
                : DEFAULT_CONFIG

            if (config.enabled === undefined) config.enabled = true

            setDbConfig(data)
            setJsonInput(JSON.stringify(config, null, 2))
        } catch (error) {
            setJsonInput(JSON.stringify(DEFAULT_CONFIG, null, 2))
            setDbConfig(null)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        if (deploymentId) fetchConfig()
    }, [deploymentId])

    const handleSave = async () => {
        try {
            const parsed = JSON.parse(jsonInput)
            await api.post("/management/config", {
                policy_type: "rate_limit",
                deployment_id: deploymentId,
                config_json: parsed
            })
            toast.success("Configuration updated successfully")
            setDbConfig({ ...dbConfig!, config_json: parsed, policy_type: "rate_limit" })
        } catch (e) {
            console.error(e)
            toast.error("Invalid JSON format")
        }
    }

    const config = JSON.parse(jsonInput)
    const updateConfig = (newConfig: any) => {
        setJsonInput(JSON.stringify(newConfig, null, 2))
    }

    return (
        <div className="space-y-6">
            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center justify-between mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider flex items-center gap-2">
                        Rate Limit Configuration
                    </h3>
                    <button
                        onClick={handleSave}
                        className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 transition-colors"
                    >
                        <Save className="w-4 h-4" /> Save Changes
                    </button>
                </div>

                <div className="space-y-8">
                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border">
                        <div>
                            <div className="font-medium text-sm">Enable Rate Limiting</div>
                            <div className="text-xs text-muted-foreground">Restrict RPM per API key or IP</div>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                            <input
                                type="checkbox"
                                className="sr-only peer"
                                checked={config.enabled}
                                onChange={(e) => updateConfig({ ...config, enabled: e.target.checked })}
                            />
                            <div className="w-11 h-6 bg-muted peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {config.enabled && (
                        <div className="grid gap-2">
                            <label className="text-sm font-medium">Requests Per Minute (RPM)</label>
                            <input
                                type="number"
                                className="flex h-10 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                value={config.rpm}
                                onChange={(e) => updateConfig({ ...config, rpm: parseInt(e.target.value) })}
                                min={1}
                            />
                            <p className="text-xs text-muted-foreground">Global limit applied to this deployment endpoint.</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
