
import { useEffect, useState } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Key, Plus, Copy } from "lucide-react"

interface ApiKey {
    id: string
    name: string
    prefix: string
    is_active: boolean
    created_at: string
}

interface ApiKeyCreated extends ApiKey {
    secret_key: string
}

export default function ApiKeys() {
    const [keys, setKeys] = useState<ApiKey[]>([])
    const [loading, setLoading] = useState(true)
    const [showAdd, setShowAdd] = useState(false)
    const [newKeyData, setNewKeyData] = useState<ApiKeyCreated | null>(null)

    // Form
    const [name, setName] = useState("")

    const fetchKeys = async () => {
        try {
            const { data } = await api.get("/management/api-keys")
            setKeys(data)
        } catch (error) {
            toast.error("Failed to fetch API keys")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        fetchKeys()
    }, [])

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault()
        try {
            const { data } = await api.post("/management/api-keys", { name })
            setNewKeyData(data)
            toast.success("API Key created")
            setShowAdd(false)
            setName("")
            fetchKeys()
        } catch (error) {
            toast.error("Failed to create key")
        }
    }

    const copyToClipboard = async (text: string) => {
        await navigator.clipboard.writeText(text)
        toast.success("Copied to clipboard")
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h2 className="text-3xl font-bold tracking-tight">API Keys</h2>
                <button
                    onClick={() => setShowAdd(!showAdd)}
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium flex items-center gap-2"
                >
                    <Plus className="w-4 h-4" /> Create Key
                </button>
            </div>

            {/* Creation Modal / Inline */}
            {showAdd && (
                <div className="p-6 bg-card rounded-xl border shadow-sm animate-in fade-in slide-in-from-top-4 mb-6">
                    <h3 className="text-lg font-medium mb-4">Create New API Key</h3>
                    <form onSubmit={handleCreate} className="space-y-4 max-w-lg">
                        <div>
                            <label className="block text-sm font-medium mb-1">Key Name</label>
                            <input
                                className="w-full p-2 border rounded-md bg-background"
                                placeholder="e.g. My App Prod"
                                value={name}
                                onChange={e => setName(e.target.value)}
                                required
                            />
                        </div>
                        <div className="flex justify-end gap-2">
                            <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm">Cancel</button>
                            <button type="submit" className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm">Create</button>
                        </div>
                    </form>
                </div>
            )}

            {/* Success Display */}
            {newKeyData && (
                <div className="p-6 bg-green-50/10 border-green-500/20 border rounded-xl mb-6">
                    <h3 className="text-lg font-medium text-green-500 mb-2">Key Created Successfully</h3>
                    <p className="text-sm text-muted-foreground mb-4">Copy this key now. You won't be able to see it again.</p>
                    <div className="flex items-center gap-2 p-3 bg-muted rounded-md border font-mono text-sm break-all">
                        <span className="flex-1">{newKeyData.secret_key}</span>
                        <button onClick={() => copyToClipboard(newKeyData.secret_key)} className="p-2 hover:bg-background rounded-md">
                            <Copy className="w-4 h-4" />
                        </button>
                    </div>
                    <button onClick={() => setNewKeyData(null)} className="mt-4 text-sm underline">Done</button>
                </div>
            )}

            {loading ? <div>Loading keys...</div> : (
                <div className="bg-card rounded-xl border shadow-sm overflow-hidden">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-muted/50 text-muted-foreground font-medium border-b">
                            <tr>
                                <th className="p-4">Name</th>
                                <th className="p-4">Key Prefix</th>
                                <th className="p-4">Created</th>
                                <th className="p-4">Status</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y">
                            {keys.map(key => (
                                <tr key={key.id} className="hover:bg-muted/30 transition-colors">
                                    <td className="p-4 font-medium flex items-center gap-2">
                                        <Key className="w-4 h-4 text-primary" />
                                        {key.name}
                                    </td>
                                    <td className="p-4 font-mono text-muted-foreground">{key.prefix}</td>
                                    <td className="p-4 text-muted-foreground">{new Date(key.created_at).toLocaleDateString()}</td>
                                    <td className="p-4 flex items-center justify-between">
                                        <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${key.is_active ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'}`}>
                                            {key.is_active ? 'Active' : 'Revoked'}
                                        </span>
                                        {key.is_active && (
                                            <button
                                                onClick={async () => {
                                                    if (confirm("Are you sure you want to revoke this key? It will stop working immediately.")) {
                                                        try {
                                                            await api.delete(`/management/api-keys/${key.id}`)
                                                            toast.success("API Key revoked")
                                                            fetchKeys()
                                                        } catch (e) {
                                                            toast.error("Failed to revoke key")
                                                        }
                                                    }
                                                }}
                                                className="text-xs text-destructive hover:underline ml-4"
                                            >
                                                Revoke
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                            {keys.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="p-8 text-center text-muted-foreground">No API keys found.</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}
