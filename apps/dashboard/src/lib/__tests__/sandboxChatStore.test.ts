import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadChat,
  saveChat,
  clearChat,
  MAX_MESSAGES,
  type ChatMessage,
} from "../sandboxChatStore";

const DEP = "dep-1";

function msg(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: over.id ?? "m1",
    role: over.role ?? "assistant",
    content: over.content ?? "hello",
    reasoning: over.reasoning,
    tokens: over.tokens,
    timestamp: over.timestamp ?? new Date("2026-06-12T00:00:00.000Z"),
  };
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("sandboxChatStore", () => {
  it("returns [] when nothing is stored", () => {
    expect(loadChat(DEP)).toEqual([]);
  });

  it("round-trips messages and revives Date timestamps", () => {
    const ts = new Date("2026-06-12T01:02:03.000Z");
    saveChat(DEP, [msg({ id: "a", content: "hi", timestamp: ts, tokens: 7 })]);
    const out = loadChat(DEP);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("a");
    expect(out[0].content).toBe("hi");
    expect(out[0].tokens).toBe(7);
    expect(out[0].timestamp).toBeInstanceOf(Date);
    expect(out[0].timestamp.getTime()).toBe(ts.getTime());
  });

  it("isolates threads per deployment id", () => {
    saveChat("dep-A", [msg({ id: "a", content: "A" })]);
    saveChat("dep-B", [msg({ id: "b", content: "B" })]);
    expect(loadChat("dep-A")[0].content).toBe("A");
    expect(loadChat("dep-B")[0].content).toBe("B");
  });

  it("clearChat removes only that deployment's thread", () => {
    saveChat("dep-A", [msg({ content: "A" })]);
    saveChat("dep-B", [msg({ content: "B" })]);
    clearChat("dep-A");
    expect(loadChat("dep-A")).toEqual([]);
    expect(loadChat("dep-B")).toHaveLength(1);
  });

  it("returns [] on corrupt JSON", () => {
    localStorage.setItem("inferia.sandbox.chat.v1::dep-1", "{not json");
    expect(loadChat(DEP)).toEqual([]);
  });

  it("returns [] on version mismatch", () => {
    localStorage.setItem(
      "inferia.sandbox.chat.v1::dep-1",
      JSON.stringify({ v: 99, messages: [{ id: "x", role: "user", content: "h", timestamp: 0 }] }),
    );
    expect(loadChat(DEP)).toEqual([]);
  });

  it("returns [] when payload.messages is not an array", () => {
    localStorage.setItem("inferia.sandbox.chat.v1::dep-1", JSON.stringify({ v: 1, messages: "nope" }));
    expect(loadChat(DEP)).toEqual([]);
  });

  it("drops malformed message entries", () => {
    localStorage.setItem(
      "inferia.sandbox.chat.v1::dep-1",
      JSON.stringify({
        v: 1,
        messages: [
          { id: "ok", role: "user", content: "good", timestamp: 0 },
          { id: "bad", role: "system", content: "x", timestamp: 0 },
          { id: "bad2", role: "user", timestamp: 0 },
        ],
      }),
    );
    const out = loadChat(DEP);
    expect(out).toHaveLength(1);
    expect(out[0].content).toBe("good");
  });

  it("caps stored messages to the last MAX_MESSAGES", () => {
    const many: ChatMessage[] = Array.from({ length: MAX_MESSAGES + 25 }, (_, i) =>
      msg({ id: String(i), content: String(i) }),
    );
    saveChat(DEP, many);
    const out = loadChat(DEP);
    expect(out).toHaveLength(MAX_MESSAGES);
    expect(out[0].content).toBe("25"); // oldest 25 dropped
    expect(out[out.length - 1].content).toBe(String(MAX_MESSAGES + 24));
  });

  it("never throws when localStorage.getItem throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadChat(DEP)).toEqual([]);
  });

  it("never throws when localStorage.setItem throws (quota)", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => saveChat(DEP, [msg()])).not.toThrow();
  });

  it("no-ops for null/empty deployment id", () => {
    expect(loadChat(null)).toEqual([]);
    expect(loadChat("")).toEqual([]);
    expect(() => saveChat(null, [msg()])).not.toThrow();
    expect(() => clearChat(undefined)).not.toThrow();
  });
});
