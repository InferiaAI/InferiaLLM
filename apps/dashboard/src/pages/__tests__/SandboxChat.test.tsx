import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInterface } from "../Sandbox";
import { loadChat } from "@/lib/sandboxChatStore";

vi.mock("@/lib/tokenStore", () => ({ getToken: () => "test-token" }));

const deployment = {
  id: "dep-1",
  name: "Test Model",
  modelName: "test-model",
  model_type: "inference",
  engine: "vllm",
  endpointUrl: "",
  status: "READY",
};

function mockChatResponse(content: string, reasoning?: string) {
  return {
    ok: true,
    json: async () => ({
      choices: [{ message: { content, reasoning_content: reasoning } }],
      usage: { completion_tokens: 12 },
    }),
  } as Response;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ChatInterface", () => {
  it("sends a message and renders the markdown answer with collapsed thinking", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockChatResponse("<think>let me reason</think>The **answer** is 42"),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "what is it?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("answer")).toBeInTheDocument());
    // user bubble present
    expect(screen.getByText("what is it?")).toBeInTheDocument();
    // thinking collapsed by default
    const thinkToggle = screen.getByRole("button", { name: /thinking/i });
    expect(thinkToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("let me reason")).not.toBeInTheDocument();
    // persisted
    await waitFor(() => expect(loadChat("dep-1")).toHaveLength(2));
  });

  it("Clear button wipes the conversation after confirm", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockChatResponse("hello there"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("hello there")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /^clear$/i })); // step 1: arm
    await user.click(screen.getByRole("button", { name: /confirm/i })); // step 2: confirm

    expect(screen.queryByText("hello there")).not.toBeInTheDocument();
    expect(loadChat("dep-1")).toEqual([]);
  });

  it("restores a stored conversation on remount", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockChatResponse("persisted reply"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    const { unmount } = render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "remember me");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("persisted reply")).toBeInTheDocument());
    unmount();

    render(<ChatInterface deployment={deployment} />);
    expect(screen.getByText("remember me")).toBeInTheDocument();
    expect(screen.getByText("persisted reply")).toBeInTheDocument();
  });
});
