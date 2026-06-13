import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSandboxChat } from "../useSandboxChat";
import { loadChat, saveChat, type ChatMessage } from "@/lib/sandboxChatStore";

function msg(id: string, content: string): ChatMessage {
  return { id, role: "user", content, timestamp: new Date("2026-06-12T00:00:00.000Z") };
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

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

    // Record every write so we can prove no STALE cross-thread write occurs
    // (the transient an effect-based hydration would produce). Final contents
    // alone can be self-healed by a later write, so we assert on the writes.
    const writes: Array<{ key: string; value: string }> = [];
    const realSet = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, k: string, v: string) {
      writes.push({ key: k, value: v });
      realSet.call(this, k, v);
    });

    const { result, rerender } = renderHook(({ id }) => useSandboxChat(id), {
      initialProps: { id: "dep-A" },
    });
    expect(result.current.messages.map((m) => m.content)).toEqual(["fromA"]);
    rerender({ id: "dep-B" });
    expect(result.current.messages.map((m) => m.content)).toEqual(["fromB"]);

    // Final contents intact.
    expect(loadChat("dep-A").map((m) => m.content)).toEqual(["fromA"]);
    expect(loadChat("dep-B").map((m) => m.content)).toEqual(["fromB"]);
    // And critically: dep-B's key was NEVER written with dep-A's payload, and
    // dep-A's key was never written with dep-B's payload (no stale clobber).
    expect(writes.some((w) => w.key.includes("dep-B") && w.value.includes("fromA"))).toBe(false);
    expect(writes.some((w) => w.key.includes("dep-A") && w.value.includes("fromB"))).toBe(false);
  });

  it("does not write to storage for a null deployment", () => {
    const { result } = renderHook(() => useSandboxChat(null));
    act(() => result.current.setMessages([msg("a", "x")]));
    expect(result.current.messages).toHaveLength(1); // in-memory only
    expect(localStorage.length).toBe(0);
  });
});
