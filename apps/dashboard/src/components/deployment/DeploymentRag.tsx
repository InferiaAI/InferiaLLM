import { useEffect, useState } from "react"
import { Save, AlertCircle, ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import api from "@/lib/api"
import { useQuery } from "@tanstack/react-query"
import { ConfigService } from "@/services/configService"
import { Link } from "react-router-dom"

interface DeploymentRagProps {
    deploymentId: string
}

interface ConfigResponse {
    policy_type: string
    config_json: Record<string, any>
    updated_at?: string
}

const DEFAULT_CONFIG = { enabled: true, top_k: 3 }

export default function DeploymentRag({ deploymentId }: DeploymentRagProps) {
    const [loading, setLoading] = useState(false)
    const [jsonInput, setJsonInput] = useState("{}")
    const [dbConfig, setDbConfig] = useState<ConfigResponse | null>(null)
    const [collections, setCollections] = useState<string[]>([])

    // Check Provider Configuration
    const { data: providers, isLoading: loadingProviders } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    const isVectorDbConfigured = providers?.vectordb.chroma.is_local || !!providers?.vectordb.chroma.api_key;

    const fetchConfig = async () => {
        setLoading(true)
        try {
            const { data } = await api.get(`/management/config/rag?deployment_id=${deploymentId}`)

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

    const fetchCollections = async () => {
        try {
            const { data } = await api.get("/management/data/collections")
            setCollections(data)
        } catch (e) {
            console.error(e)
        }
    }

    useEffect(() => {
        if (deploymentId) {
            fetchConfig()
            fetchCollections()
        }
    }, [deploymentId])

    const handleSave = async () => {
        try {
            const parsed = JSON.parse(jsonInput)
            await api.post("/management/config", {
                policy_type: "rag",
                deployment_id: deploymentId,
                config_json: parsed
            })
            toast.success("Configuration updated successfully")
            setDbConfig({ ...dbConfig!, config_json: parsed, policy_type: "rag" })
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
                        RAG Configuration
                    </h3>
                    <button
                        onClick={handleSave}
                        disabled={loading}
                        className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                        <Save className="w-4 h-4" /> Save Changes
                    </button>
                </div>

                <div className="space-y-8">
                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border">
                        <div>
                            <div className="font-medium text-sm">Enable RAG</div>
                            <div className="text-xs text-muted-foreground">Augment generation with knowledge base context</div>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                            <input
                                type="checkbox"
                                className="sr-only peer"
                                checked={config.enabled !== false}
                                onChange={(e) => updateConfig({ ...config, enabled: e.target.checked })}
                            />
                            <div className="w-11 h-6 bg-muted peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-background after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {loadingProviders ? (
                        <div className="flex flex-col items-center justify-center p-8 gap-3">
                            <Loader2 className="w-6 h-6 animate-spin text-primary" />
                            <p className="text-xs text-muted-foreground animate-pulse">Verifying Vector DB configuration...</p>
                        </div>
                    ) : !isVectorDbConfigured ? (
                        <div className="p-8 bg-orange-500/5 border border-orange-500/20 rounded-xl flex flex-col items-center text-center gap-4">
                            <div className="w-12 h-12 bg-orange-500/10 text-orange-500 rounded-full flex items-center justify-center">
                                <AlertCircle className="w-6 h-6" />
                            </div>
                            <div>
                                <h4 className="font-bold">Vector Database Not Configured</h4>
                                <p className="text-sm text-muted-foreground max-w-sm">
                                    RAG requires a connected Vector Database to retrieve document context.
                                </p>
                            </div>
                            <Link
                                to="/dashboard/settings/providers/vectordb/chroma"
                                className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors group"
                            >
                                Configure ChromaDB <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                            </Link>
                        </div>
                    ) : config.enabled && (
                        <div className="grid gap-6">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium">Knowledge Base Collection</label>
                                <select
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                    value={config.default_collection || ""}
                                    onChange={(e) => updateConfig({ ...config, default_collection: e.target.value })}
                                >
                                    <option value="">Select a collection...</option>
                                    {collections.map((c: string) => (
                                        <option key={c} value={c}>{c}</option>
                                    ))}
                                </select>
                            </div>

                            <div className="grid gap-2">
                                <label className="text-sm font-medium">Top K Results</label>
                                <input
                                    type="number"
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                    value={config.top_k || 3}
                                    onChange={(e) => updateConfig({ ...config, top_k: parseInt(e.target.value) })}
                                    min={1}
                                    max={20}
                                />
                                <p className="text-xs text-muted-foreground">Number of relevant chunks to retrieve (1-20)</p>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
