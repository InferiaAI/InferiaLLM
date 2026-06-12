import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CodeBlock } from "./CodeBlock";

afterEach(() => vi.restoreAllMocks());

describe("CodeBlock", () => {
  it("renders the code and a language label", () => {
    render(<CodeBlock code="print('hi')" language="python" />);
    expect(screen.getByText("print('hi')")).toBeInTheDocument();
    expect(screen.getByText("python")).toBeInTheDocument();
  });

  it("falls back to 'code' when no language is given", () => {
    render(<CodeBlock code="x = 1" />);
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("copies the code to the clipboard on click", async () => {
    const user = userEvent.setup();
    // Override AFTER setup(): userEvent installs its own navigator.clipboard stub.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CodeBlock code="copy me" language="ts" />);
    await user.click(screen.getByRole("button", { name: /copy code/i }));
    expect(writeText).toHaveBeenCalledWith("copy me");
  });
});
