import { useState } from "react"
import api from "@/lib/api"
import { toast } from "sonner"
import { Database, Plus, Upload, Loader2, FileText, Trash2, FolderOpen } from "lucide-react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"

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

    // Collections Query
    const { data: collections, isLoading: loadingCollections } = useQuery<string[]>({
        queryKey: ["collections"],
        queryFn: async () => {
            const { data } = await api.get("/management/data/collections")
            return data
        }
    })

    // Files Query (Dependent)
    const { data: files, isLoading: loadingFiles } = useQuery<KBFile[]>({
        queryKey: ["files", selectedCollection],
        queryFn: async () => {
            if (!selectedCollection) return []
            const { data } = await api.get(`/management/data/collections/${selectedCollection}/files`)
            return data
        },
        enabled: !!selectedCollection
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
            // Invalidate both collections (if new) and files
            queryClient.invalidateQueries({ queryKey: ["collections"] })
            queryClient.invalidateQueries({ queryKey: ["files", selectedCollection] })

            // If we created a new collection, select it? 
            // Hard to know name if dynamic, but usually user selects it.
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

    // Auto-select first collection if none selected and loaded
    if (!selectedCollection && collections && collections.length > 0) {
        setSelectedCollection(collections[0])
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
                        // Default to current selection if exists
                        setIsNew(false)
                        setNewCollectionName("")
                    }}
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium flex items-center gap-2 hover:bg-primary/90 transition-colors"
                >
                    <Plus className="w-4 h-4" /> Add Data
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
                                <div className="flex gap-2">
                                    {/* Actions like Clear Collection could go here */}
                                </div>
                            </div>

                            <div className="flex-1 overflow-y-auto p-6">
                                {loadingFiles ? (
                                    <div className="grid gap-4">
                                        {[1, 2, 3].map(i => <div key={i} className="h-16 bg-muted/50 rounded-lg animate-pulse" />)}
                                    </div>
                                ) : files?.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center h-full text-center p-8 border-2 border-dashed border-muted rounded-xl bg-muted/5">
                                        <Upload className="w-10 h-10 text-muted-foreground mb-4 opacity-50" />
                                        <h4 className="text-lg font-medium">No files found</h4>
                                        <p className="text-muted-foreground text-sm max-w-sm mt-2">
                                            This collection is empty. Upload documents to start using RAG.
                                        </p>
                                        <button
                                            onClick={() => setShowUpload(true)}
                                            className="mt-4 px-4 py-2 text-primary text-sm font-medium hover:underline"
                                        >
                                            Upload Document
                                        </button>
                                    </div>
                                ) : (
                                    <div className="grid gap-3">
                                        {files?.map((file, idx) => (
                                            <div key={idx} className="flex items-center justify-between p-4 bg-muted/10 border rounded-lg hover:border-primary/20 hover:shadow-sm transition-all group">
                                                <div className="flex items-center gap-3 overflow-hidden">
                                                    <div className="p-2 bg-background border rounded text-muted-foreground">
                                                        <FileText className="w-5 h-5" />
                                                    </div>
                                                    <div>
                                                        <div className="font-medium truncate max-w-[300px]" title={file.filename}>{file.filename}</div>
                                                        <div className="text-xs text-muted-foreground flex items-center gap-2">
                                                            <span>{file.doc_count} Chunks</span>
                                                            <span>•</span>
                                                            <span>Est. Size: {(file.doc_count * 1).toFixed(1)} KB</span>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                                    {/* Info or Delete buttons */}
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

            {/* Upload Modal Overlay */}
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
                                <p className="text-xs text-muted-foreground mt-1">
                                    {file ? `${(file.size / 1024).toFixed(1)} KB` : "Supports PDF, DOCX, TXT"}
                                </p>
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
