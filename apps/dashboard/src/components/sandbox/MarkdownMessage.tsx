import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./CodeBlock";

/**
 * Renders assistant Markdown with GitHub-flavored extensions (tables, task
 * lists, strikethrough, autolinks). Raw HTML is NOT enabled (no rehype-raw),
 * so script/event-handler injection in model output is inert; react-markdown's
 * default url transform additionally strips dangerous link protocols.
 */
const components: Components = {
  a: ({ children, ...props }) => (
    <a
      {...props}
      target="_blank"
      rel="noopener noreferrer"
      className="text-ember-600 underline underline-offset-2 hover:text-ember-700"
    >
      {children}
    </a>
  ),
  // Fenced code blocks arrive wrapped in <pre><code>; render them via CodeBlock.
  pre: ({ children }) => {
    const child = Array.isArray(children) ? children[0] : children;
    const childProps =
      child && typeof child === "object" && "props" in child
        ? (child as { props: { className?: string; children?: unknown } }).props
        : {};
    const className = childProps.className || "";
    const match = /language-(\w+)/.exec(className);
    const code = String(childProps.children ?? "").replace(/\n$/, "");
    return <CodeBlock code={code} language={match?.[1]} />;
  },
  // Anything reaching `code` directly is inline code.
  code: ({ children, ...props }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]" {...props}>
      {children}
    </code>
  ),
  h1: ({ children }) => <h1 className="mt-1 mb-1.5 text-base font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-1 mb-1.5 text-[0.95rem] font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-1 mb-1 text-sm font-semibold">{children}</h3>,
  ul: ({ children }) => <ul className="my-1 list-disc space-y-0.5 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1 list-decimal space-y-0.5 pl-5">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="my-1 border-l-2 border-border pl-3 italic text-muted-foreground">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-border bg-muted/50 px-2 py-1 text-left font-medium">{children}</th>,
  td: ({ children }) => <td className="border border-border px-2 py-1">{children}</td>,
  hr: () => <hr className="my-2 border-border" />,
};

export function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="space-y-2 break-words text-sm leading-relaxed [&>p]:my-1">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
