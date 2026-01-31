import { Skeleton } from "@/components/ui/Skeleton"

export function OverviewSkeleton() {
    return (
        <div className="space-y-8 animate-in fade-in-50 duration-500">
            {/* Header */}
            <div className="flex flex-col gap-2">
                <Skeleton className="h-8 w-48" />
                <Skeleton className="h-4 w-64" />
            </div>

            {/* Stats Grid */}
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
                {[...Array(4)].map((_, i) => (
                    <div key={i} className="p-6 bg-card rounded-xl border shadow-sm">
                        <div className="flex items-start justify-between mb-4">
                            <Skeleton className="h-10 w-10 rounded-lg" />
                            <div className="space-y-2">
                                <Skeleton className="h-3 w-20 ml-auto" />
                                <Skeleton className="h-8 w-12 ml-auto" />
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <Skeleton className="h-4 w-4 rounded-full" />
                            <Skeleton className="h-4 w-24" />
                        </div>
                    </div>
                ))}
            </div>

            {/* Recent Activity Table */}
            <div className="space-y-4">
                <Skeleton className="h-6 w-48" />
                <div className="border rounded-xl overflow-hidden bg-card shadow-sm">
                    <div className="border-b bg-muted/30 p-4">
                        <div className="flex gap-4">
                            <Skeleton className="h-4 w-1/5" />
                            <Skeleton className="h-4 w-1/5" />
                            <Skeleton className="h-4 w-1/5" />
                            <Skeleton className="h-4 w-1/5" />
                            <Skeleton className="h-4 w-1/5" />
                        </div>
                    </div>
                    <div className="p-0">
                        {[...Array(5)].map((_, i) => (
                            <div key={i} className="flex gap-4 p-4 border-b last:border-0 items-center">
                                <Skeleton className="h-4 w-1/5" />
                                <Skeleton className="h-4 w-1/5" />
                                <Skeleton className="h-4 w-1/5" />
                                <Skeleton className="h-4 w-1/5" />
                                <Skeleton className="h-5 w-16 rounded-full" />
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    )
}
