
import { useEffect, useState } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { FileText, Plus, Trash2 } from "lucide-react"
import { useQueryClient, useMutation } from "@tanstack/react-query"

interface PromptTemplate {
    template_id: string
    content: string
    description?: string
    updated_at: string
}

export default function Templates() {
    const [templates, setTemplates] = useState<PromptTemplate[]>([])
    const [loading, setLoading] = useState(true)
    const [showAdd, setShowAdd] = useState(false)

    // Form
    const [templateId, setTemplateId] = useState("")
    const [content, setContent] = useState("")
    const [description, setDescription] = useState("")

    const fetchTemplates = async () => {
        try {
            const { data } = await api.get("/management/templates")
            setTemplates(data)
            setLoading(false)
        } catch (error) {
            toast.error("Failed to fetch templates")
            setLoading(false)
        }
    }

    useEffect(() => {
        fetchTemplates()
    }, [])

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault()
        try {
            await api.post("/management/templates", {
                template_id: templateId,
                content,
                description
            })
            toast.success("Template created")
            setShowAdd(false)
            setTemplateId("")
            setContent("")
            setDescription("")
            fetchTemplates()
        } catch (error) {
            toast.error("Failed to create template")
        }
    }

    // Delete Mutation
    const deleteMutation = useMutation({
        mutationFn: async (id: string) => {
            await api.delete(`/management/templates/${id}`)
        },
        onSuccess: () => {
            toast.success("Template deleted successfully")
            queryClient.invalidateQueries({ queryKey: ["templates"] }) // Ensure queryKey matches fetch
            fetchTemplates() // Since fetchTemplates is manual here, call it too or switch to useQuery fully later. 
            // simpler to re-call fetchTemplates for now as useQuery isn't fully set up in this file for list?
            // Wait, looking at code, it uses useEffect + fetchTemplates. 
        },
        onError: (err: any) => {
            toast.error(err.response?.data?.detail || "Failed to delete template")
        }
    })

    const handleDelete = async (id: string) => {
        if (confirm("Are you sure you want to delete this template?")) {
            deleteMutation.mutate(id)
        }
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h2 className="text-3xl font-bold tracking-tight">Prompt Templates</h2>
                <button
                    onClick={() => setShowAdd(!showAdd)}
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium flex items-center gap-2"
                >
                    <Plus className="w-4 h-4" /> Create Template
                </button>
            </div>

            {/* Creation Modal / Inline */}
            {showAdd && (
                <div className="p-6 bg-card rounded-xl border shadow-sm animate-in fade-in slide-in-from-top-4 mb-6">
                    <h3 className="text-lg font-medium mb-4">Create New Template</h3>
                    <form onSubmit={handleCreate} className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium mb-1">Template ID</label>
                            <input
                                className="w-full p-2 border rounded-md bg-background"
                                placeholder="e.g. customer-support"
                                value={templateId}
                                onChange={e => setTemplateId(e.target.value)}
                                required
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Description</label>
                            <input
                                className="w-full p-2 border rounded-md bg-background"
                                placeholder="e.g. Support agent style"
                                value={description}
                                onChange={e => setDescription(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Content (Jinja2)</label>
                            <textarea
                                className="w-full p-2 border rounded-md bg-background font-mono text-sm h-40"
                                placeholder="You are a helpful assistant. Context: {{ context }}"
                                value={content}
                                onChange={e => setContent(e.target.value)}
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

            {loading ? <div>Loading templates...</div> : (
                <div className="bg-card rounded-xl border shadow-sm overflow-hidden">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-muted/50 text-muted-foreground font-medium border-b">
                            <tr>
                                <th className="p-4">ID</th>
                                <th className="p-4">Description</th>
                                <th className="p-4">Last Updated</th>
                                <th className="p-4 w-[50px]"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y relative">
                            {templates.map(tmpl => (
                                <tr key={tmpl.template_id} className="hover:bg-muted/30 transition-colors group">
                                    <td className="p-4 font-medium flex items-center gap-2">
                                        <FileText className="w-4 h-4 text-primary" />
                                        {tmpl.template_id}
                                    </td>
                                    <td className="p-4 text-muted-foreground">{tmpl.description || "-"}</td>
                                    <td className="p-4 text-muted-foreground">{new Date(tmpl.updated_at).toLocaleDateString()}</td>
                                    <td className="p-4">
                                        <button
                                            onClick={() => handleDelete(tmpl.template_id)}
                                            className="text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100 p-2"
                                            title="Delete Template"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                            {templates.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="p-8 text-center text-muted-foreground">No templates found.</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}
