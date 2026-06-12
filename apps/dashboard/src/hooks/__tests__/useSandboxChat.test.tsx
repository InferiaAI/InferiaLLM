import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useSandboxChat } from "../useSandboxChat";
import { loadChat, saveChat, type ChatMessage } from "@/lib/sandboxChatStore";

function msg(id: string, content: string): ChatMessage {
  return { id, role: "user", content, timestamp: new Date("2026-06-12T00:00:00.000Z") };
}

beforeEach(() => localStorage.clear());

describe("useSandboxChat", () => {
  it("hydrates from the store on mount", () => {
    saveChat("dep-1", [msg("a", "stored")]);
    const { result } = renderHook(() => useSandboxChat("dep-1"));
    expect(result.current.messages.map((m) => m.content)).toEqual(["stored"]);
    expect(result.current.hydrated).toBe(true);
  });

  it("persists appended messages", () => {
    const { result } = renderHook(() => useSandboxChat("dep-1"));
    act(() => result.current.setMessages([msg("a", "new")]));
    expect(loadChat("dep-1").map((m) => m.content)).toEqual(["new"]);
  });

  it("clear() empties state and storage", () => {
    saveChat("dep-1", [msg("a", "x")]);
    const { result } = renderHook(() => useSandboxChat("dep-1"));
    act(() => result.current.clear());
    expect(result.current.messages).toEqual([]);
    expect(loadChat("dep-1")).toEqual([]);
  });

  it("swaps threads when the deployment id changes, without clobbering", () => {
    saveChat("dep-A", [msg("a", "fromA")]);
    saveChat("dep-B", [msg("b", "fromB")]);
    const { result, rerender } = renderHook(({ id }) => useSandboxChat(id), {
      initialProps: { id: "dep-A" },
    });
    expect(result.current.messages.map((m) => m.content)).toEqual(["fromA"]);
    rerender({ id: "dep-B" });
    expect(result.current.messages.map((m) => m.content)).toEqual(["fromB"]);
    // A must not have been overwritten with B's (or empty) messages.
    expect(loadChat("dep-A").map((m) => m.content)).toEqual(["fromA"]);
    expect(loadChat("dep-B").map((m) => m.content)).toEqual(["fromB"]);
  });

  it("does not write to storage for a null deployment", () => {
    const { result } = renderHook(() => useSandboxChat(null));
    act(() => result.current.setMessages([msg("a", "x")]));
    expect(result.current.messages).toHaveLength(1); // in-memory only
    expect(localStorage.length).toBe(0);
  });
});
