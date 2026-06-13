/**
 * Unwrap react-markdown's `<pre>` child (a `<code>` element) into the raw code
 * string + language. Pure function, kept in its own module so its defensive
 * branches (unexpected child shapes, missing className/children) are
 * unit-testable without rendering. react-markdown passes a single code
 * element, but we guard against arrays and malformed nodes.
 */
export function extractFencedCode(children: unknown): { code: string; language?: string } {
  const child = Array.isArray(children) ? children[0] : children;
  const props =
    child && typeof child === "object" && "props" in child
      ? (child as { props: { className?: string; children?: unknown } }).props
      : undefined;
  const className = props?.className ?? "";
  const match = /language-(\w+)/.exec(className);
  const code = String(props?.children ?? "").replace(/\n$/, "");
  return { code, language: match?.[1] };
}
