import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatInterface, ChatMessageItem } from "./ChatInterface";
import { loadChat, saveChat, type ChatMessage } from "@/lib/sandboxChatStore";

vi.mock("@/lib/tokenStore", () => ({ getToken: () => "test-token" }));

const deployment = { id: "dep-1", modelName: "test-model" };

function okResponse(content: string, reasoning?: string) {
  return {
    ok: true,
    json: async () => ({
      choices: [{ message: { content, reasoning_content: reasoning } }],
      usage: { completion_tokens: 12 },
    }),
  } as Response;
}

function lastRequestBody(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls.at(-1);
  return JSON.parse((call?.[1] as RequestInit).body as string);
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("ChatInterface", () => {
  it("sends a message and renders the markdown answer with collapsed thinking", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse("<think>let me reason</think>The **answer** is 42"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "what is it?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("answer")).toBeInTheDocument());
    expect(screen.getByText("what is it?")).toBeInTheDocument();
    const thinkToggle = screen.getByRole("button", { name: /thinking/i });
    expect(thinkToggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("let me reason")).not.toBeInTheDocument();
    expect(screen.getByText("12 tok")).toBeInTheDocument();
    await waitFor(() => expect(loadChat("dep-1")).toHaveLength(2));
  });

  it("strips <think> from prior assistant turns when sending follow-up history", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse("<think>secret</think>The answer is 42"))
      .mockResolvedValueOnce(okResponse("follow up reply"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "q1");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("The answer is 42")).toBeInTheDocument());

    await user.type(screen.getByPlaceholderText(/type your message/i), "q2");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const body = lastRequestBody(fetchMock);
    const assistantTurn = body.messages.find((m: { role: string }) => m.role === "assistant");
    expect(assistantTurn.content).toBe("The answer is 42"); // no <think> re-sent
    expect(assistantTurn.content).not.toContain("<think>");
  });

  it("prepends a system prompt to the request when one is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse("ok"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.click(screen.getByRole("button", { name: /system prompt/i }));
    await user.type(screen.getByPlaceholderText(/optional system prompt/i), "be terse");
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = lastRequestBody(fetchMock);
    expect(body.messages[0]).toEqual({ role: "system", content: "be terse" });
  });

  it("surfaces an API error via a toast and does not append an assistant message", async () => {
    const { toast } = await import("sonner");
    const toastErr = vi.spyOn(toast, "error").mockImplementation(() => "id");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(toastErr).toHaveBeenCalledWith("boom"));
    // user message persisted, but no assistant reply
    await waitFor(() => expect(loadChat("dep-1")).toHaveLength(1));
  });

  it("uses a separate reasoning_content field and omits the token count when usage is absent", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ choices: [{ message: { content: "the answer", reasoning_content: "deep thought" } }] }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("the answer")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /thinking/i })).toBeInTheDocument(); // reasoning surfaced
    expect(screen.queryByText(/tok$/)).not.toBeInTheDocument(); // no usage → no token footer
  });

  it("shows the placeholder when the API returns an empty message", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ choices: [{ message: {} }] }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(screen.getByText("No response generated")).toBeInTheDocument());
  });

  it("falls back to a generic API error when the error body is unparseable", async () => {
    const { toast } = await import("sonner");
    const toastErr = vi.spyOn(toast, "error").mockImplementation(() => "id");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => {
        throw new Error("no body");
      },
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(toastErr).toHaveBeenCalledWith("API Error: 503"));
  });

  it("copies the assistant answer (think-stripped) to the clipboard", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse("<think>x</think>just the answer"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("just the answer")).toBeInTheDocument());

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    // Both the user and assistant bubbles have a copy button — the assistant's
    // is the last one.
    const copyButtons = screen.getAllByRole("button", { name: /copy message/i });
    await user.click(copyButtons[copyButtons.length - 1]);
    expect(writeText).toHaveBeenCalledWith("just the answer");
  });

  it("Clear button wipes the conversation after a two-step confirm", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse("hello there"));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ChatInterface deployment={deployment} />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText("hello there")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /^clear$/i }));
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(screen.queryByText("hello there")).not.toBeInTheDocument();
    expect(loadChat("dep-1")).toEqual([]);
  });

  it("reverts the Clear confirm after the timeout if not confirmed", () => {
    vi.useFakeTimers();
    // Seed a stored thread so Clear is enabled on mount (no async send needed).
    saveChat("dep-1", [{ id: "a", role: "user", content: "seed", timestamp: new Date(0) }]);
    render(<ChatInterface deployment={deployment} />);

    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    expect(screen.getByRole("button", { name: /confirm/i })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(3100));
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    // The conversation was NOT cleared (only armed then timed out).
    expect(loadChat("dep-1")).toHaveLength(1);
  });

  it("restores a stored conversation on remount", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse("persisted reply"));
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

describe("ChatMessageItem", () => {
  const base = (over: Partial<ChatMessage>): ChatMessage => ({
    id: "m",
    role: "assistant",
    content: "",
    timestamp: new Date("2026-06-12T00:00:00.000Z"),
    ...over,
  });

  it("renders a thinking-only turn without an empty answer bubble or footer", () => {
    render(<ChatMessageItem message={base({ content: "<think>just reasoning</think>" })} />);
    expect(screen.getByRole("button", { name: /thinking/i })).toBeInTheDocument();
    expect(screen.queryByText("No response generated")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /copy message/i })).not.toBeInTheDocument();
  });

  it("shows a placeholder when the assistant returned nothing at all", () => {
    render(<ChatMessageItem message={base({ content: "" })} />);
    expect(screen.getByText("No response generated")).toBeInTheDocument();
  });

  it("does not mislabel a reasoning-only response as 'No response generated'", () => {
    render(<ChatMessageItem message={base({ content: "", reasoning: "thought hard" })} />);
    expect(screen.queryByText("No response generated")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /thinking/i })).toBeInTheDocument();
  });

  it("renders a user message as plain text", () => {
    render(<ChatMessageItem message={base({ role: "user", content: "hi there" })} />);
    expect(screen.getByText("hi there")).toBeInTheDocument();
  });

  it("resets the copied indicator after the timeout", () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
    const { container } = render(<ChatMessageItem message={base({ content: "hello answer" })} />);
    fireEvent.click(screen.getByRole("button", { name: /copy message/i }));
    expect(container.querySelector(".lucide-check")).not.toBeNull();
    act(() => vi.advanceTimersByTime(2100));
    expect(container.querySelector(".lucide-check")).toBeNull();
  });

  it("copy falls back to raw content for a placeholder (no answer) turn", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<ChatMessageItem message={base({ content: "" })} />);
    fireEvent.click(screen.getByRole("button", { name: /copy message/i }));
    expect(writeText).toHaveBeenCalledWith("");
  });
});
