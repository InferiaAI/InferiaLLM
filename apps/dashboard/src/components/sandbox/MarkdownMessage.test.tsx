import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownMessage } from "./MarkdownMessage";
import { extractFencedCode } from "./markdownUtils";

describe("extractFencedCode", () => {
  it("pulls code + language from a code element", () => {
    const child = { props: { className: "language-python", children: "print(1)\n" } };
    expect(extractFencedCode(child)).toEqual({ code: "print(1)", language: "python" });
  });

  it("returns undefined language when there is no language- class", () => {
    expect(extractFencedCode({ props: { className: "", children: "x" } })).toEqual({
      code: "x",
      language: undefined,
    });
  });

  it("unwraps an array of children", () => {
    const child = { props: { className: "language-ts", children: "let a = 1" } };
    expect(extractFencedCode([child])).toEqual({ code: "let a = 1", language: "ts" });
  });

  it("falls back to empty code for malformed / non-object children", () => {
    expect(extractFencedCode(null)).toEqual({ code: "", language: undefined });
    expect(extractFencedCode("just a string")).toEqual({ code: "", language: undefined });
    expect(extractFencedCode({ props: {} })).toEqual({ code: "", language: undefined });
  });
});

describe("MarkdownMessage", () => {
  it("renders headings, bold, and lists", () => {
    render(<MarkdownMessage content={"# Title\n\n**bold** text\n\n- one\n- two"} />);
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByText("bold")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders inline code", () => {
    const { container } = render(<MarkdownMessage content={"use `npm run build` now"} />);
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("npm run build");
  });

  it("renders a fenced code block via CodeBlock (language label + copy)", () => {
    render(<MarkdownMessage content={"```python\nprint('hi')\n```"} />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText("print('hi')")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy code/i })).toBeInTheDocument();
  });

  it("renders links opening safely in a new tab without leaking the node prop", () => {
    render(<MarkdownMessage content={"[site](https://example.com)"} />);
    const link = screen.getByRole("link", { name: "site" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("href", "https://example.com");
    // react-markdown's internal `node` prop must not leak onto the DOM.
    expect(link).not.toHaveAttribute("node");
  });

  it("renders GFM tables", () => {
    render(<MarkdownMessage content={"| A | B |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
  });

  it("does not render raw HTML as elements (XSS-safe)", () => {
    const { container } = render(
      <MarkdownMessage content={'<script>window.__x=1</script><img src=x onerror="alert(1)">'} />,
    );
    // A rehype-raw regression would create real <script>/<img onerror> elements;
    // these structural assertions trip if raw HTML is ever enabled.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img[onerror]")).toBeNull();
    // The raw markup must appear as escaped text instead.
    expect(container.textContent).toContain("<script>");
    expect(container.textContent).toContain("<img");
  });

  it("renders a fenced code block with no language label", () => {
    render(<MarkdownMessage content={"```\nplain code\n```"} />);
    expect(screen.getByText("plain code")).toBeInTheDocument();
    expect(screen.getByText("code")).toBeInTheDocument(); // CodeBlock's default label
  });

  it("renders ordered lists, sub-headings, blockquote, and a horizontal rule", () => {
    const { container } = render(
      <MarkdownMessage content={"## H2\n\n### H3\n\n1. first\n2. second\n\n> a quote\n\n---"} />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "H2" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "H3" })).toBeInTheDocument();
    expect(container.querySelector("ol")).not.toBeNull();
    expect(screen.getByText("a quote")).toBeInTheDocument();
    expect(container.querySelector("hr")).not.toBeNull();
  });

  it("sanitizes javascript: links", () => {
    // react-markdown strips the dangerous protocol — the href is dropped
    // entirely (so the anchor loses its 'link' role). Assert on the element.
    const { container } = render(<MarkdownMessage content={"[x](javascript:alert(1))"} />);
    expect(screen.getByText("x")).toBeInTheDocument();
    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("href") ?? "").not.toContain("javascript:");
  });
});
