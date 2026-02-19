import React from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./button";

interface EmptyStateProps {
    icon: LucideIcon;
    title: string;
    description: string;
    action?: {
        label: string;
        onClick: () => void;
    };
    className?: string;
}

export function EmptyState({
    icon: Icon,
    title,
    description,
    action,
    className,
}: EmptyStateProps) {
    return (
        <div
            className={cn(
                "flex flex-col items-center justify-center py-12 px-4 text-center border-2 border-dashed border-slate-200 dark:border-zinc-800 rounded-lg bg-slate-50/50 dark:bg-zinc-900/50",
                className
            )}
        >
            <div className="bg-white dark:bg-zinc-800 p-3 rounded-full shadow-sm mb-4">
                <Icon className="w-8 h-8 text-slate-400 dark:text-zinc-500" />
            </div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-zinc-100 mb-1">
                {title}
            </h3>
            <p className="text-sm text-slate-500 dark:text-zinc-400 max-w-sm mb-6">
                {description}
            </p>
            {action && (
                <Button onClick={action.onClick} variant="default">
                    {action.label}
                </Button>
            )}
        </div>
    );
}
