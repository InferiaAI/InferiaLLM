import { useEffect, useState } from "react"
import { Shield, Save, Cpu, AlertCircle, ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import api from "@/lib/api"
import { useQuery } from "@tanstack/react-query"
import { ConfigService } from "@/services/configService"
import { Link } from "react-router-dom"

interface DeploymentGuardrailsProps {
    deploymentId: string
}

interface ConfigResponse {
    policy_type: string
    config_json: Record<string, any>
    updated_at?: string
}

const DEFAULT_CONFIG = {
    enabled: true,
    guardrail_engine: "llm-guard", // Default to old engine
    input_scanners: [],
    output_scanners: [],
    toxicity_threshold: 0.5
}

export default function DeploymentGuardrails({ deploymentId }: DeploymentGuardrailsProps) {
    const [loading, setLoading] = useState(false)
    const [jsonInput, setJsonInput] = useState("{}")
    const [dbConfig, setDbConfig] = useState<ConfigResponse | null>(null)

    // Check Provider Configuration
    const { data: providers, isLoading: loadingProviders } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    const isEngineConfigured = (engine: string) => {
        if (!providers) return false;
        if (engine === "llm-guard") return true; // Local engine
        if (engine === "llama-guard") return !!providers.guardrails.groq.api_key;
        if (engine === "lakera-guard") return !!providers.guardrails.lakera.api_key;
        return false;
    }

    const fetchConfig = async () => {
        setLoading(true)
        try {
            const { data } = await api.get(`/management/config/guardrail?deployment_id=${deploymentId}`)

            const config = (data.config_json && Object.keys(data.config_json).length > 0)
                ? data.config_json
                : DEFAULT_CONFIG

            // Ensure enabled is set if missing
            if (config.enabled === undefined) config.enabled = true
            // Ensure engine is set
            if (!config.guardrail_engine) config.guardrail_engine = "llm-guard"

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
                policy_type: "guardrail",
                deployment_id: deploymentId,
                config_json: parsed
            })
            toast.success("Configuration updated successfully")
            setDbConfig({ ...dbConfig!, config_json: parsed, policy_type: "guardrail" })
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
        <div className="bg-card rounded-xl border shadow-sm p-6">
            <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-medium">Configure Guardrails</h3>
                <button
                    onClick={handleSave}
                    disabled={loading}
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium flex items-center gap-2 disabled:opacity-50"
                >
                    <Save className="w-4 h-4" /> Save
                </button>
            </div>

            <div className="space-y-6">
                {/* Enable Toggle */}
                <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border">
                    <div>
                        <div className="font-medium">Enable Guardrails</div>
                        <div className="text-sm text-muted-foreground">Master switch for all guardrails</div>
                    </div>
                    <input
                        type="checkbox"
                        className="w-5 h-5 accent-primary"
                        checked={config.enabled !== false}
                        onChange={(e) => updateConfig({ ...config, enabled: e.target.checked })}
                    />
                </div>

                {/* Proceed on Violation Toggle */}
                <div className="flex items-center justify-between p-4 bg-yellow-50 dark:bg-yellow-950/20 rounded-lg border border-yellow-200 dark:border-yellow-900">
                    <div>
                        <div className="font-medium flex items-center gap-2 text-yellow-800 dark:text-yellow-500">
                            Proceed on Violation
                            <span className="text-[10px] bg-yellow-100 dark:bg-yellow-900 px-1 rounded border border-yellow-300 dark:border-yellow-800 uppercase">Warning</span>
                        </div>
                        <div className="text-sm text-yellow-700 dark:text-yellow-600">
                            Do not block request on violation. Instead, append warning to system prompt.
                        </div>
                    </div>
                    <input
                        type="checkbox"
                        className="w-5 h-5 accent-yellow-600"
                        checked={config.proceed_on_violation === true}
                        onChange={(e) => updateConfig({ ...config, proceed_on_violation: e.target.checked })}
                    />
                </div>

                {/* Engine Selection */}
                <div className="space-y-3">
                    <label className="text-sm font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                        <Cpu className="w-4 h-4" /> Guardrail Engine
                    </label>
                    <select
                        className="w-full p-2 border rounded-md bg-background"
                        value={config.guardrail_engine || "llm-guard"}
                        onChange={(e) => updateConfig({ ...config, guardrail_engine: e.target.value })}
                    >
                        <option value="llm-guard">LLM Guard (Local Standards)</option>
                        <option value="llama-guard">Llama Guard 4 (Groq)</option>
                        <option value="lakera-guard">Lakera Guard (API)</option>
                    </select>

                    {loadingProviders ? (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground p-2">
                            <Loader2 className="w-3 h-3 animate-spin" /> Verifying provider configuration...
                        </div>
                    ) : !isEngineConfigured(config.guardrail_engine) && (
                        <div className="p-4 bg-orange-500/5 border border-orange-500/20 rounded-lg flex flex-col items-center text-center gap-3">
                            <AlertCircle className="w-8 h-8 text-orange-500" />
                            <div>
                                <h4 className="font-bold text-sm">Provider Not Connected</h4>
                                <p className="text-xs text-muted-foreground">
                                    You need to configure your {config.guardrail_engine === "llama-guard" ? "Groq" : "Lakera"} API key in settings to use this engine.
                                </p>
                            </div>
                            <Link
                                to={config.guardrail_engine === "llama-guard" ? "/dashboard/settings/providers/guardrails/groq" : "/dashboard/settings/providers/guardrails/lakera"}
                                className="text-xs font-bold text-primary flex items-center gap-1 hover:underline"
                            >
                                Configure {config.guardrail_engine === "llama-guard" ? "Groq" : "Lakera"} <ArrowRight className="w-3 h-3" />
                            </Link>
                        </div>
                    )}

                    {config.guardrail_engine === "llama-guard" && isEngineConfigured("llama-guard") && (
                        <p className="text-xs text-muted-foreground bg-blue-50 dark:bg-blue-950/20 text-blue-600 dark:text-blue-400 p-2 rounded">
                            Uses meta-llama/llama-guard-4-12b via Groq. Optimized for chat safety (Violence, Hate, Sexual Content, etc.).
                        </p>
                    )}
                    {config.guardrail_engine === "lakera-guard" && isEngineConfigured("lakera-guard") && (
                        <p className="text-xs text-muted-foreground bg-purple-50 dark:bg-purple-950/20 text-purple-600 dark:text-purple-400 p-2 rounded">
                            Uses Lakera AI API. Gold standard for Prompt Injection & Jailbreak detection. Requires LAKERA_API_KEY.
                        </p>
                    )}
                </div>

                {isEngineConfigured(config.guardrail_engine) && (
                    <>
                        {config.guardrail_engine === "llm-guard" && (
                            <div className="space-y-6 mt-6 border-t pt-6">
                                <div className="space-y-4">
                                    <h4 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Input Scanners</h4>
                                    <div className="grid grid-cols-2 gap-3">
                                        {[
                                            { id: "Toxicity", label: "Toxicity" },
                                            { id: "PromptInjection", label: "Prompt Injection" },
                                            { id: "Secrets", label: "Secrets Detection" },
                                            { id: "Code", label: "Malicious Code" }
                                        ].map(scanner => (
                                            <label key={scanner.id} className="flex items-center gap-2 p-2 border rounded-md hover:bg-muted/50 cursor-pointer transition-colors text-sm">
                                                <input
                                                    type="checkbox"
                                                    className="w-4 h-4 accent-primary"
                                                    checked={config.input_scanners?.includes(scanner.id)}
                                                    onChange={(e) => {
                                                        const scanners = new Set(config.input_scanners || [])
                                                        if (e.target.checked) scanners.add(scanner.id)
                                                        else scanners.delete(scanner.id)
                                                        updateConfig({ ...config, input_scanners: Array.from(scanners) })
                                                    }}
                                                />
                                                <span>{scanner.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>

                                <div className="space-y-4">
                                    <h4 className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Output Scanners</h4>
                                    <div className="grid grid-cols-2 gap-3">
                                        {[
                                            { id: "Toxicity", label: "Toxicity" },
                                            { id: "Sensitive", label: "Sensitive Info (PII)" },
                                            { id: "Bias", label: "Bias" },
                                            { id: "NoRefusal", label: "No Refusal" },
                                            { id: "Relevance", label: "Relevance" }
                                        ].map(scanner => (
                                            <label key={scanner.id} className="flex items-center gap-2 p-2 border rounded-md hover:bg-muted/50 cursor-pointer transition-colors text-sm">
                                                <input
                                                    type="checkbox"
                                                    className="w-4 h-4 accent-primary"
                                                    checked={config.output_scanners?.includes(scanner.id)}
                                                    onChange={(e) => {
                                                        const scanners = new Set(config.output_scanners || [])
                                                        if (e.target.checked) scanners.add(scanner.id)
                                                        else scanners.delete(scanner.id)
                                                        updateConfig({ ...config, output_scanners: Array.from(scanners) })
                                                    }}
                                                />
                                                <span>{scanner.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {config.guardrail_engine === "llama-guard" && (
                            <div className="space-y-6 mt-6 border-t pt-6">
                                <div className="space-y-3">
                                    <label className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Llama Guard Safety Categories</label>
                                    <div className="grid grid-cols-2 gap-3">
                                        {[
                                            { id: "violent_crimes", label: "Violent Crimes" },
                                            { id: "non_violent_crimes", label: "Non-Violent Crimes" },
                                            { id: "sex_related_crimes", label: "Sex-Related Crimes" },
                                            { id: "child_exploitation", label: "Child Exploitation" },
                                            { id: "defamation", label: "Defamation" },
                                            { id: "specialized_advice", label: "Specialized Advice" },
                                            { id: "privacy", label: "Privacy" },
                                            { id: "intellectual_property", label: "Intellectual Property" },
                                            { id: "indiscriminate_weapons", label: "Indiscriminate Weapons" },
                                            { id: "hate", label: "Hate" },
                                            { id: "suicide_self_harm", label: "Suicide & Self-Harm" },
                                            { id: "sexual_content", label: "Sexual Content" },
                                            { id: "elections", label: "Elections" },
                                            { id: "code_interpreter_abuse", label: "Code Interpreter Abuse" }
                                        ].map(scanner => (
                                            <label key={scanner.id} className="flex items-center gap-2 p-2 border rounded-md hover:bg-muted/50 cursor-pointer transition-colors text-sm">
                                                <input
                                                    type="checkbox"
                                                    className="w-4 h-4 accent-primary"
                                                    checked={config.input_scanners?.includes(scanner.id)}
                                                    onChange={(e) => {
                                                        const scanners = new Set(config.input_scanners || [])
                                                        if (e.target.checked) scanners.add(scanner.id)
                                                        else scanners.delete(scanner.id)
                                                        updateConfig({ ...config, input_scanners: Array.from(scanners) })
                                                    }}
                                                />
                                                <span>{scanner.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {config.guardrail_engine === "lakera-guard" && (
                            <div className="space-y-6 mt-6 border-t pt-6">
                                <div className="space-y-3">
                                    <label className="text-sm font-bold text-muted-foreground uppercase tracking-wider">Lakera Guard Detection</label>
                                    <div className="grid grid-cols-2 gap-3">
                                        {[
                                            { id: "prompt_injection", label: "Prompt Injection / Jailbreak" },
                                            { id: "hate", label: "Hate Speech" },
                                            { id: "sexual_content", label: "Sexual Content" },
                                            { id: "violent_crimes", label: "Violence" },
                                            { id: "toxicity", label: "Toxicity (General)" },
                                            { id: "pii", label: "PII (Lakera Native)" }
                                        ].map(scanner => (
                                            <label key={scanner.id} className="flex items-center gap-2 p-2 border rounded-md hover:bg-muted/50 cursor-pointer transition-colors text-sm">
                                                <input
                                                    type="checkbox"
                                                    className="w-4 h-4 accent-primary"
                                                    checked={config.input_scanners?.includes(scanner.id)}
                                                    onChange={(e) => {
                                                        const scanners = new Set(config.input_scanners || [])
                                                        if (e.target.checked) scanners.add(scanner.id)
                                                        else scanners.delete(scanner.id)
                                                        updateConfig({ ...config, input_scanners: Array.from(scanners) })
                                                    }}
                                                />
                                                <span>{scanner.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* PII Redaction (Independent Service) */}
                        <div className="space-y-4 pt-2 border-t">
                            <div className="flex items-center justify-between">
                                <label className="text-sm font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                                    <Shield className="w-4 h-4" /> PII Redaction
                                </label>
                                <div className="flex items-center gap-2">
                                    <span className="text-sm text-muted-foreground">
                                        {config.pii_enabled ? "Enabled" : "Disabled"}
                                    </span>
                                    <input
                                        type="checkbox"
                                        className="w-5 h-5 accent-primary"
                                        checked={config.pii_enabled === true}
                                        onChange={(e) => updateConfig({ ...config, pii_enabled: e.target.checked })}
                                    />
                                </div>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Redact sensitive information before it reaches the model or guardrails.
                                Works with both LLM Guard and Llama Guard.
                            </p>

                            {config.pii_enabled && (
                                <div className="space-y-3 p-4 bg-muted/30 rounded-lg">
                                    <label className="text-sm font-medium">PII Entities to Redact</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        {[
                                            { key: "EMAIL_ADDRESS", label: "Email Address" },
                                            { key: "PHONE_NUMBER", label: "Phone Number" },
                                            { key: "CREDIT_CARD", label: "Credit Card" },
                                            { key: "US_SSN", label: "SSN" },
                                            { key: "IP_ADDRESS", label: "IP Address" },
                                            { key: "PERSON", label: "Person Name" },
                                            { key: "LOCATION", label: "Location" },
                                            { key: "DATE_TIME", label: "Date/Time" },
                                            { key: "URL", label: "URLs" },
                                            { key: "PASSWORD", label: "Passwords" },
                                        ].map(entity => (
                                            <label key={entity.key} className="flex items-center gap-2 p-2 border rounded-md hover:bg-muted/50 cursor-pointer transition-colors text-sm">
                                                <input
                                                    type="checkbox"
                                                    className="w-4 h-4 accent-primary"
                                                    checked={
                                                        (() => {
                                                            const entities = config.pii_entities || ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IP_ADDRESS", "PERSON", "LOCATION"]
                                                            return entities.includes(entity.key)
                                                        })()
                                                    }
                                                    onChange={(e) => {
                                                        const defaultEntities = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IP_ADDRESS", "PERSON", "LOCATION"]
                                                        const entities = new Set(config.pii_entities || defaultEntities)
                                                        if (e.target.checked) entities.add(entity.key)
                                                        else entities.delete(entity.key)
                                                        updateConfig({ ...config, pii_entities: Array.from(entities) })
                                                    }}
                                                />
                                                <span>{entity.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            )
                            }
                        </div>
                    </>
                )}
            </div>
        </div>
    )
}
