import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThinkingBlock } from "./ThinkingBlock";

describe("ThinkingBlock", () => {
  it("renders nothing when thinking is empty or null", () => {
    const { container: c1 } = render(<ThinkingBlock thinking={null} />);
    expect(c1).toBeEmptyDOMElement();
    const { container: c2 } = render(<ThinkingBlock thinking="   " />);
    expect(c2).toBeEmptyDOMElement();
  });

  it("is collapsed by default (content hidden, aria-expanded false)", () => {
    render(<ThinkingBlock thinking="secret reasoning" />);
    const toggle = screen.getByRole("button", { name: /thinking/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("secret reasoning")).not.toBeInTheDocument();
  });

  it("expands on click to reveal the reasoning", async () => {
    const user = userEvent.setup();
    render(<ThinkingBlock thinking="secret reasoning" />);
    const toggle = screen.getByRole("button", { name: /thinking/i });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("secret reasoning")).toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});
