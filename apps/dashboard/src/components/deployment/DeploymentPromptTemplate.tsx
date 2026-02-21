import { useEffect, useState } from "react"
import { Save } from "lucide-react"
import { toast } from "sonner"
import api from "@/lib/api"

interface DeploymentPromptTemplateProps {
    deploymentId: string
}

interface ConfigResponse {
    policy_type: string
    config_json: Record<string, any>
    updated_at?: string
}

const DEFAULT_CONFIG = { enabled: true }

export default function DeploymentPromptTemplate({ deploymentId }: DeploymentPromptTemplateProps) {
    const [loading, setLoading] = useState(false)
    const [jsonInput, setJsonInput] = useState("{}")
    const [dbConfig, setDbConfig] = useState<ConfigResponse | null>(null)
    const [templates, setTemplates] = useState<any[]>([])
    // We also need collections for variable mapping if source is rag
    const [collections, setCollections] = useState<string[]>([])

    const fetchConfig = async () => {
        setLoading(true)
        try {
            const { data } = await api.get(`/management/config/prompt_template?deployment_id=${deploymentId}`)

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

    const fetchData = async () => {
        try {
            const [tmpls, cols] = await Promise.all([
                api.get("/management/templates"),
                api.get("/management/data/collections")
            ])
            setTemplates(tmpls.data)
            setCollections(cols.data)
        } catch (e) {
            console.error(e)
        }
    }

    useEffect(() => {
        if (deploymentId) {
            fetchConfig()
            fetchData()
        }
    }, [deploymentId])

    const handleSave = async () => {
        try {
            const parsed = JSON.parse(jsonInput)
            await api.post("/management/config", {
                policy_type: "prompt_template",
                deployment_id: deploymentId,
                config_json: parsed
            })
            toast.success("Configuration updated successfully")
            setDbConfig({ ...dbConfig!, config_json: parsed, policy_type: "prompt_template" })
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
                        Prompt Construction
                    </h3>
                    <button
                        onClick={handleSave}
                        className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 transition-colors"
                    >
                        <Save className="w-4 h-4" /> Save Changes
                    </button>
                </div>

                <div className="space-y-6">
                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border">
                        <div>
                            <div className="font-medium text-sm">Enable Templates</div>
                            <div className="text-xs text-muted-foreground">Wrap input in a structured prompt format</div>
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
                        <div className="space-y-8 mt-6">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium">Base Template</label>
                                <select
                                    className="flex h-10 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                    value={config.base_template_id || ""}
                                    onChange={(e) => {
                                        const newConfig = { ...config }
                                        newConfig.base_template_id = e.target.value

                                        const selectedTmpl = templates.find(t => t.template_id === e.target.value)
                                        if (selectedTmpl) {
                                            const matches = selectedTmpl.content.match(/\{\{\s*(\w+)\s*\}\}/g)
                                            const vars = matches ? matches.map((m: string) => m.replace(/\{\{\s*|\s*\}\}/g, '')) : []
                                            const uniqueVars = [...new Set(vars)] as string[]

                                            const mapping = newConfig.variable_mapping || {}
                                            uniqueVars.forEach((v) => {
                                                if (!mapping[v]) mapping[v] = { source: "request", key: v }
                                            })
                                            newConfig.variable_mapping = mapping
                                        }

                                        updateConfig(newConfig)
                                    }}
                                >
                                    <option value="">Select a Template...</option>
                                    {templates.map((t: any) => (
                                        <option key={t.template_id} value={t.template_id}>
                                            {t.template_id}
                                        </option>
                                    ))}
                                </select>
                            </div>

                            {config.variable_mapping && Object.keys(config.variable_mapping).length > 0 && (
                                <div className="space-y-4">
                                    <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wide border-b pb-2">Variable Mapping</h4>

                                    <div className="grid gap-4">
                                        {Object.entries(config.variable_mapping).map(([varName, varConfig]: [string, any]) => (
                                            <div key={varName} className="grid grid-cols-12 gap-4 items-center p-3 border rounded-md bg-background hover:bg-muted/50 transition-colors">
                                                <div className="col-span-3 font-mono text-sm bg-muted/50 p-2 rounded text-center truncate" title={varName}>{varName}</div>
                                                <div className="col-span-1 text-center text-muted-foreground">←</div>
                                                <div className="col-span-3">
                                                    <select
                                                        className="flex h-9 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                                        value={varConfig.source}
                                                        onChange={(e) => {
                                                            const newConfig = { ...config }
                                                            newConfig.variable_mapping[varName] = { ...varConfig, source: e.target.value }
                                                            updateConfig(newConfig)
                                                        }}
                                                    >
                                                        <option value="request">Request Payload</option>
                                                        <option value="rag">RAG Knowledge Base</option>
                                                        <option value="static">Static Value</option>
                                                    </select>
                                                </div>
                                                <div className="col-span-5">
                                                    {varConfig.source === "rag" ? (
                                                        <select
                                                            className="flex h-9 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                                            value={varConfig.collection_id || ""}
                                                            onChange={(e) => {
                                                                const newConfig = { ...config }
                                                                newConfig.variable_mapping[varName] = { ...varConfig, collection_id: e.target.value }
                                                                updateConfig(newConfig)
                                                            }}
                                                        >
                                                            <option value="">Select Collection...</option>
                                                            {collections.map(c => <option key={c} value={c}>{c}</option>)}
                                                        </select>
                                                    ) : varConfig.source === "static" ? (
                                                        <input
                                                            className="flex h-9 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                                            placeholder="Static Value"
                                                            value={varConfig.value || ""}
                                                            onChange={(e) => {
                                                                const newConfig = { ...config }
                                                                newConfig.variable_mapping[varName] = { ...varConfig, value: e.target.value }
                                                                updateConfig(newConfig)
                                                            }}
                                                        />
                                                    ) : (
                                                        <input
                                                            className="flex h-9 w-full rounded-md border border-input bg-white dark:bg-zinc-900 text-slate-900 dark:text-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                                            placeholder="JSON Key (default: same as var)"
                                                            value={varConfig.key || varName}
                                                            onChange={(e) => {
                                                                const newConfig = { ...config }
                                                                newConfig.variable_mapping[varName] = { ...varConfig, key: e.target.value }
                                                                updateConfig(newConfig)
                                                            }}
                                                        />
                                                    )}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Advanced / Override Section */}
                            <div className="pt-4 border-t">
                                <details className="group">
                                    <summary className="flex cursor-pointer list-none items-center justify-between font-medium">
                                        <span className="text-sm text-muted-foreground hover:text-foreground transition-colors">Advanced: Custom Override</span>
                                        <span className="transition group-open:rotate-180">
                                            <svg fill="none" height="24" shapeRendering="geometricPrecision" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" viewBox="0 0 24 24" width="24"><path d="M6 9l6 6 6-6"></path></svg>
                                        </span>
                                    </summary>
                                    <div className="text-neutral-600 group-open:animate-fadeIn mt-3 text-sm">
                                        <textarea
                                            className="min-h-[200px] w-full rounded-md border border-input bg-zinc-950 text-zinc-50 font-mono px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                            placeholder="{{ system_prompt }} ... {{ user_input }}"
                                            value={config.content || ""}
                                            onChange={(e) => updateConfig({ ...config, content: e.target.value })}
                                        />
                                        <p className="mt-2 text-xs text-muted-foreground">This content overrides the base template selection. Use standard handlebars syntax.</p>
                                    </div>
                                </details>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
