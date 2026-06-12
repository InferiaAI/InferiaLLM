import { useState } from "react";
import { Check, Copy } from "lucide-react";

/**
 * A fenced code block: language label + copy button + horizontally scrollable
 * pre. No syntax-token highlighting (kept dependency-free); the dark surface
 * is fixed so code reads the same in light and dark themes.
 */
export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    void navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border bg-[#0d1117]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wide text-slate-400">{language || "code"}</span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label="Copy code"
          className="text-slate-400 transition-colors hover:text-slate-100"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed text-slate-100">
        <code>{code}</code>
      </pre>
    </div>
  );
}
