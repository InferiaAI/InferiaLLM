import { useNavigate } from "react-router-dom";
import { Cloud, Database, Shield, Cpu, ChevronRight } from "lucide-react";

export default function ProviderCategories() {
    const navigate = useNavigate();

    const categories = [
        {
            id: "cloud",
            title: "Infrastructure & Compute",
            description: "Manage Cloud (AWS, GCP) and DePIN (Nosana, Akash) credentials",
            icon: Cloud,
            color: "text-blue-500",
            bg: "bg-blue-50 dark:bg-blue-900/20",
        },
        {
            id: "vector-db",
            title: "Vector Database",
            description: "Connect Chroma, Pinecone, or Weaviate",
            icon: Database,
            color: "text-purple-500",
            bg: "bg-purple-50 dark:bg-purple-900/20",
        },
        {
            id: "guardrails",
            title: "Guardrails",
            description: "Configure Groq, Lakera, and other security providers",
            icon: Shield,
            color: "text-green-500",
            bg: "bg-green-50 dark:bg-green-900/20",
        },
    ];

    return (
        <div className="max-w-5xl mx-auto space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Providers</h1>
                <p className="text-muted-foreground mt-2">
                    Manage your infrastructure and third-party service connections.
                </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {categories.map((category) => (
                    <button
                        key={category.id}
                        onClick={() => navigate(`/dashboard/settings/providers/${category.id}`)}
                        className="flex items-start gap-4 p-6 rounded-xl border border-border bg-card hover:bg-accent/50 hover:border-accent-foreground/20 transition-all text-left group shadow-sm"
                    >
                        <div className={`p-3 rounded-lg ${category.bg} ${category.color}`}>
                            <category.icon className="w-6 h-6" />
                        </div>
                        <div className="flex-1">
                            <h3 className="font-semibold text-lg flex items-center gap-2">
                                {category.title}
                                <ChevronRight className="w-4 h-4 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all text-muted-foreground" />
                            </h3>
                            <p className="text-sm text-muted-foreground mt-1">
                                {category.description}
                            </p>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
}
