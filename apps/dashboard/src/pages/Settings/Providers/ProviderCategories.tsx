import { Link } from "react-router-dom";
import { Cloud, ChevronRight, Database } from "lucide-react";

export default function ProviderCategories() {
    const categories = [
        {
            id: "cloud",
            title: "Infrastructure & Compute",
            description: "Manage Cloud (AWS, GCP) and DePIN (Nosana, Akash) credentials",
            icon: Cloud,
            color: "text-ember-500",
            bg: "bg-ember-50 dark:bg-ember-900/20",
        },
        {
            id: "huggingface",
            title: "Hugging Face",
            description: "Access token for caching gated/private models from the HF Hub",
            icon: Database,
            color: "text-yellow-600",
            bg: "bg-yellow-50 dark:bg-yellow-900/20",
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
                    <Link
                        key={category.id}
                        to={`/dashboard/settings/providers/${category.id}`}
                        className="flex items-start gap-4 p-6 rounded-xl border border-border bg-card hover:bg-accent/50 hover:border-accent-foreground/20 transition-colors text-left group shadow-sm"
                    >
                        <div className={`p-3 rounded-lg ${category.bg} ${category.color}`}>
                            <category.icon className="w-6 h-6" aria-hidden="true" />
                        </div>
                        <div className="flex-1">
                            <h3 className="font-semibold text-lg flex items-center gap-2">
                                {category.title}
                                <ChevronRight className="w-4 h-4 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-colors text-muted-foreground" aria-hidden="true" />
                            </h3>
                            <p className="text-sm text-muted-foreground mt-1">
                                {category.description}
                            </p>
                        </div>
                    </Link>
                ))}
            </div>
        </div>
    );
}
