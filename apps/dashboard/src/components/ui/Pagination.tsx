import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface PaginationProps {
    currentPage: number;
    totalPages: number;
    onPageChange: (page: number) => void;
    pageSize: number;
    onPageSizeChange: (size: number) => void;
    totalItems: number;
}

export function Pagination({
    currentPage,
    totalPages,
    onPageChange,
    pageSize,
    onPageSizeChange,
    totalItems
}: PaginationProps) {
    const startItem = (currentPage - 1) * pageSize + 1;
    const endItem = Math.min(currentPage * pageSize, totalItems);

    return (
        <div className="flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-zinc-900/50 border-t dark:border-zinc-800">
            <div className="text-xs text-slate-500 dark:text-zinc-500 font-mono">
                Showing {startItem}-{endItem} of {totalItems} items
            </div>
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500 dark:text-zinc-500">Rows per page:</span>
                    <select
                        value={pageSize}
                        onChange={(e) => onPageSizeChange(Number(e.target.value))}
                        className="bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded px-2 py-0.5 text-xs outline-none focus:ring-1 focus:ring-emerald-500"
                    >
                        <option value={10}>10</option>
                        <option value={20}>20</option>
                        <option value={50}>50</option>
                        <option value={100}>100</option>
                    </select>
                </div>
                <div className="flex items-center gap-1">
                    <button
                        onClick={() => onPageChange(currentPage - 1)}
                        disabled={currentPage <= 1}
                        className={cn(
                            "p-1 rounded hover:bg-slate-200 dark:hover:bg-zinc-800 transition-colors",
                            currentPage <= 1 && "opacity-50 cursor-not-allowed"
                        )}
                    >
                        <ChevronLeft className="w-4 h-4" />
                    </button>
                    <span className="text-xs text-slate-500 dark:text-zinc-500 font-mono px-2">
                        Page {currentPage} of {totalPages || 1}
                    </span>
                    <button
                        onClick={() => onPageChange(currentPage + 1)}
                        disabled={currentPage >= totalPages}
                        className={cn(
                            "p-1 rounded hover:bg-slate-200 dark:hover:bg-zinc-800 transition-colors",
                            currentPage >= totalPages && "opacity-50 cursor-not-allowed"
                        )}
                    >
                        <ChevronRight className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    );
}
