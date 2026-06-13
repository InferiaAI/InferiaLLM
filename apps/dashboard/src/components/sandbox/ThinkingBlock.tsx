import { useState } from "react";
import { Brain, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Collapsible disclosure for model reasoning. Collapsed by default; renders
 * nothing when there is no thinking. Reasoning is shown as preformatted text
 * (it is usually plain prose, occasionally light markup we deliberately keep
 * literal to avoid surprising rendering of half-finished thoughts).
 */
export function ThinkingBlock({ thinking }: { thinking: string | null | undefined }) {
  const [open, setOpen] = useState(false);
  if (!thinking || !thinking.trim()) return null;

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-border/70 bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/50"
      >
        <Brain className="h-3.5 w-3.5" />
        <span className="font-medium">Thinking</span>
        <ChevronDown className={cn("ml-auto h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="whitespace-pre-wrap border-t border-border/60 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {thinking}
        </div>
      )}
    </div>
  );
}
