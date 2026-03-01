import { useState } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Database, Plus, Upload, Loader2, FileText, Trash2, FolderOpen, AlertCircle, ArrowRight } from "lucide-react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { ConfigService } from "@/services/configService"
import { Link } from "react-router-dom"

interface KBFile {
    filename: string
    doc_id: string
    uploaded_by: string
    doc_count: number
}

export default function KnowledgeBase() {
    const queryClient = useQueryClient()
    const [selectedCollection, setSelectedCollection] = useState<string | null>(null)
    const [showUpload, setShowUpload] = useState(false)

    // Check Configuration
    const { data: config, isLoading: loadingConfig } = useQuery({
        queryKey: ["providerConfig"],
        queryFn: () => ConfigService.getProviderConfig()
    })

    const isVectorDbConfigured = config ? (
        config.vectordb.chroma.is_local
            ? !!config.vectordb.chroma.url
            : !!config.vectordb.chroma.api_key
    ) : false

    // Collections Query
    const { data: collections, isLoading: loadingCollections, error: collectionError } = useQuery<string[]>({
        queryKey: ["collections"],
        queryFn: async () => {
            const { data } = await api.get("/management/data/collections")
            return data
        },
        enabled: !!config && isVectorDbConfigured,
        retry: false
    })

    // Files Query (Dependent)
    const { data: files, isLoading: loadingFiles } = useQuery<KBFile[]>({
        queryKey: ["files", selectedCollection],
        queryFn: async () => {
            if (!selectedCollection) return []
            const { data } = await api.get(`/management/data/collections/${selectedCollection}/files`)
            return data
        },
        enabled: !!selectedCollection && isVectorDbConfigured
    })

    // Upload Mutation
    const uploadMutation = useMutation({
        mutationFn: async (formData: FormData) => {
            await api.post("/management/data/upload", formData, {
                headers: { "Content-Type": "multipart/form-data" }
            })
        },
        onSuccess: () => {
            toast.success("Document uploaded successfully")
            setShowUpload(false)
            queryClient.invalidateQueries({ queryKey: ["collections"] })
            queryClient.invalidateQueries({ queryKey: ["files", selectedCollection] })
        },
        onError: (err: any) => {
            toast.error(err.response?.data?.detail || "Upload failed")
        }
    })

    // Form State
    const [newCollectionName, setNewCollectionName] = useState("")
    const [isNew, setIsNew] = useState(false)
    const [file, setFile] = useState<File | null>(null)

    const handleUpload = async (e: React.FormEvent) => {
        e.preventDefault()
        if (!file) return

        const targetCollection = isNew ? newCollectionName : selectedCollection
        if (!targetCollection) {
            toast.error("Please select or create a collection")
            return
        }

        const formData = new FormData()
        formData.append("file", file)
        formData.append("collection_name", targetCollection)

        uploadMutation.mutate(formData)
    }

    // Auto-select first collection
    if (!selectedCollection && collections && collections.length > 0) {
        setSelectedCollection(collections[0])
    }

    if (loadingConfig) {
        return (
            <div className="flex flex-col items-center justify-center h-full gap-4">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
                <p className="text-muted-foreground animate-pulse">Checking configuration...</p>
            </div>
        )
    }

    if (!isVectorDbConfigured || collectionError) {
        return (
            <div className="flex flex-col items-center justify-center h-[calc(100vh-200px)] text-center p-6">
                <div className="w-20 h-20 bg-orange-500/10 text-orange-500 rounded-full flex items-center justify-center mb-6">
                    <AlertCircle className="w-10 h-10" />
                </div>
                <h2 className="text-2xl font-bold mb-2">Vector Database Not Configured</h2>
                <p className="text-muted-foreground max-w-md mb-8">
                    Knowledge Base requires a connected Vector Database to store and retrieve document embeddings.
                </p>
                <Link
                    to="/dashboard/settings/providers/vectordb/chroma"
                    className="inline-flex items-center gap-2 px-6 py-3 bg-primary text-primary-foreground rounded-lg font-medium hover:bg-primary/90 transition-all group"
                >
                    Configure ChromaDB
                    <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </Link>
            </div>
        )
    }

    return (
        <div className="space-y-6 h-[calc(100vh-100px)] flex flex-col">
            <div className="flex items-center justify-between shrink-0">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Knowledge Base</h2>
                    <p className="text-muted-foreground">Manage your RAG collections and ingested documents.</p>
                </div>
                <button
                    onClick={() => {
                        setShowUpload(true)
                        setIsNew(false)
                        setNewCollectionName("")
                    }}
                    className="px-4 py-2 bg-transparent border text-emerald-500 border-emerald-500/50 hover:bg-emerald-500/10 rounded-md text-sm font-medium flex items-center gap-2 transition-colors"
                >
                    Add new knowledge <Plus className="w-4 h-4" />
                </button>
            </div>

            <div className="flex-1 grid grid-cols-1 md:grid-cols-4 gap-6 min-h-0">
                {/* Left Sidebar: Collections List */}
                <div className="md:col-span-1 bg-card rounded-xl border shadow-sm overflow-hidden flex flex-col">
                    <div className="p-4 border-b bg-muted/30">
                        <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wider">Collections</h3>
                    </div>
                    <div className="flex-1 overflow-y-auto p-2 space-y-1">
                        {loadingCollections ? (
                            <div className="space-y-2 p-2">
                                {[1, 2, 3].map(i => <div key={i} className="h-10 bg-muted/50 rounded animate-pulse" />)}
                            </div>
                        ) : collections?.length === 0 ? (
                            <div className="text-center p-8 text-muted-foreground text-sm">
                                No collections found.
                            </div>
                        ) : (
                            collections?.map(col => (
                                <button
                                    key={col}
                                    onClick={() => setSelectedCollection(col)}
                                    className={cn(
                                        "w-full flex items-center gap-3 p-3 text-sm rounded-lg transition-colors text-left",
                                        selectedCollection === col
                                            ? "bg-primary/10 text-primary font-medium"
                                            : "hover:bg-muted text-foreground"
                                    )}
                                >
                                    <Database className="w-4 h-4 shrink-0" />
                                    <span className="truncate">{col}</span>
                                </button>
                            ))
                        )}
                    </div>
                </div>

                {/* Right Panel: File List & Details */}
                <div className="md:col-span-3 bg-card rounded-xl border shadow-sm flex flex-col overflow-hidden">
                    {selectedCollection ? (
                        <>
                            <div className="p-6 border-b flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-primary/10 text-primary rounded-lg">
                                        <FolderOpen className="w-5 h-5" />
                                    </div>
                                    <div>
                                        <h3 className="font-bold text-lg">{selectedCollection}</h3>
                                        <p className="text-xs text-muted-foreground">
                                            {files?.length || 0} documents ingested
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div className="flex-1 overflow-y-auto p-6">
                                {loadingFiles ? (
                                    <div className="grid gap-4">
                                        {[1, 2, 3].map(i => <div key={i} className="h-16 bg-muted/50 rounded-lg animate-pulse" />)}
                                    </div>
                                ) : files?.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center h-full text-center p-8">
                                        <div className="w-12 h-12 bg-muted/20 rounded-xl flex items-center justify-center mb-4 text-muted-foreground">
                                            <Database className="w-6 h-6" />
                                        </div>
                                        <h4 className="text-lg font-medium text-foreground">No Knowledge</h4>
                                        <p className="text-muted-foreground text-sm max-w-sm mt-1 mb-6">
                                            Get started by adding knowledge to this collection.
                                        </p>
                                        <button
                                            onClick={() => setShowUpload(true)}
                                            className="px-4 py-2 bg-transparent border text-emerald-500 border-emerald-500/50 hover:bg-emerald-500/10 rounded-md text-sm font-medium flex items-center gap-2 transition-colors"
                                        >
                                            Create new knowledge <Plus className="w-4 h-4" />
                                        </button>
                                    </div>
                                ) : (
                                    <div className="flex flex-col">
                                        {files?.map((file, idx) => (
                                            <div key={idx} className="flex items-center justify-between p-4 bg-transparent border-b border-border/50 hover:bg-muted/10 transition-colors group first:border-t-0">
                                                <div className="flex items-center gap-4 overflow-hidden">
                                                    <div className="p-2.5 bg-emerald-500/10 text-emerald-500 dark:text-emerald-400 rounded-lg shrink-0">
                                                        <FileText className="w-5 h-5" />
                                                    </div>
                                                    <div>
                                                        <div className="font-medium text-foreground truncate max-w-[400px]" title={file.filename}>{file.filename}</div>
                                                        <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                                                            <span>{file.doc_count} Chunks</span>
                                                            <span>•</span>
                                                            <span>{(file.doc_count * 1).toFixed(1)} KB</span>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </>
                    ) : (
                        <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-8">
                            <Database className="w-12 h-12 mb-4 opacity-20" />
                            <p>Select a collection to view files</p>
                        </div>
                    )}
                </div>
            </div>

            {showUpload && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4">
                    <div className="bg-card w-full max-w-lg rounded-xl border shadow-lg animate-in fade-in zoom-in-95 p-6">
                        <h3 className="text-xl font-bold mb-6">Ingest Document</h3>
                        <form onSubmit={handleUpload} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium mb-1.5">Target Collection</label>
                                <div className="flex gap-2 mb-3">
                                    <button
                                        type="button"
                                        onClick={() => setIsNew(false)}
                                        className={cn(
                                            "flex-1 py-1.5 text-xs font-medium rounded-md border transition-colors",
                                            !isNew ? "bg-primary text-primary-foreground border-primary" : "bg-muted hover:bg-muted/80"
                                        )}
                                    >
                                        Existing
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setIsNew(true)}
                                        className={cn(
                                            "flex-1 py-1.5 text-xs font-medium rounded-md border transition-colors",
                                            isNew ? "bg-primary text-primary-foreground border-primary" : "bg-muted hover:bg-muted/80"
                                        )}
                                    >
                                        Create New
                                    </button>
                                </div>

                                {isNew ? (
                                    <input
                                        className="w-full px-3 py-2 border rounded-md bg-background focus:ring-2 focus:ring-primary/20 outline-none"
                                        placeholder="Collection Name (e.g. legal-docs)"
                                        value={newCollectionName}
                                        onChange={e => setNewCollectionName(e.target.value)}
                                        required={isNew}
                                        autoFocus
                                    />
                                ) : (
                                    <select
                                        className="w-full px-3 py-2 border rounded-md bg-background focus:ring-2 focus:ring-primary/20 outline-none"
                                        value={selectedCollection || ""}
                                        onChange={e => setSelectedCollection(e.target.value)}
                                        required={!isNew}
                                    >
                                        <option value="" disabled>Select Collection...</option>
                                        {collections?.map(c => <option key={c} value={c}>{c}</option>)}
                                    </select>
                                )}
                            </div>

                            <div
                                className="border-2 border-dashed border-muted-foreground/25 rounded-xl p-8 flex flex-col items-center justify-center text-center hover:bg-muted/30 transition-colors cursor-pointer"
                                onClick={() => document.getElementById("file-upload")?.click()}
                            >
                                <Upload className="w-8 h-8 text-muted-foreground mb-3" />
                                <input
                                    type="file"
                                    id="file-upload"
                                    className="hidden"
                                    accept=".txt,.pdf,.docx,.md"
                                    onChange={e => setFile(e.target.files?.[0] || null)}
                                />
                                <div className="text-sm font-medium">
                                    {file ? (
                                        <span className="text-primary">{file.name}</span>
                                    ) : (
                                        "Click to upload or drag and drop"
                                    )}
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-4">
                                <button
                                    type="button"
                                    onClick={() => setShowUpload(false)}
                                    className="px-4 py-2 text-sm font-medium hover:underline"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={uploadMutation.isPending || (!file)}
                                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium flex items-center gap-2 disabled:opacity-50 hover:bg-primary/90 transition-colors"
                                >
                                    {uploadMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                                    Start Ingestion
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
