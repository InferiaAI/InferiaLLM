import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CodeBlock } from "./CodeBlock";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

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

  it("shows a check icon then reverts after the timeout", () => {
    // Synchronous fireEvent — userEvent + fake timers deadlock on internal delays.
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
    const { container } = render(<CodeBlock code="x" language="ts" />);
    fireEvent.click(screen.getByRole("button", { name: /copy code/i }));
    expect(container.querySelector(".lucide-check")).not.toBeNull();
    act(() => vi.advanceTimersByTime(2100));
    expect(container.querySelector(".lucide-check")).toBeNull();
    expect(container.querySelector(".lucide-copy")).not.toBeNull();
  });
});
