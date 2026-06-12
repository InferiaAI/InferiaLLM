import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownMessage } from "./MarkdownMessage";

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

  it("renders links opening safely in a new tab", () => {
    render(<MarkdownMessage content={"[site](https://example.com)"} />);
    const link = screen.getByRole("link", { name: "site" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("href", "https://example.com");
  });

  it("renders GFM tables", () => {
    render(<MarkdownMessage content={"| A | B |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
  });

  it("does not execute raw HTML (XSS-safe)", () => {
    const { container } = render(
      <MarkdownMessage content={'<script>window.__x=1</script><img src=x onerror="window.__x=1">'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __x?: number }).__x).toBeUndefined();
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
