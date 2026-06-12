import { describe, expect, it } from "vitest";
import { parseThinking } from "../parseThinking";

describe("parseThinking", () => {
  it("returns content as answer when there are no tags", () => {
    expect(parseThinking("just an answer")).toEqual({ thinking: null, answer: "just an answer" });
  });

  it("extracts a single <think> block", () => {
    const r = parseThinking("<think>reasoning here</think>The answer");
    expect(r.thinking).toBe("reasoning here");
    expect(r.answer).toBe("The answer");
  });

  it("extracts multiple blocks joined together", () => {
    const r = parseThinking("<think>one</think>A<think>two</think>B");
    expect(r.thinking).toBe("one\n\ntwo");
    expect(r.answer).toBe("AB");
  });

  it("is case-insensitive", () => {
    const r = parseThinking("<THINK>hmm</THINK>done");
    expect(r.thinking).toBe("hmm");
    expect(r.answer).toBe("done");
  });

  it("handles an unclosed <think> (truncated / streaming)", () => {
    const r = parseThinking("partial answer <think>still reasoning...");
    expect(r.answer).toBe("partial answer");
    expect(r.thinking).toBe("still reasoning...");
  });

  it("content that is only thinking yields an empty answer", () => {
    const r = parseThinking("<think>only thinking</think>");
    expect(r.thinking).toBe("only thinking");
    expect(r.answer).toBe("");
  });

  it("uses the reasoning field and prepends it ahead of tag content", () => {
    const r = parseThinking("<think>tag</think>final", "field reasoning");
    expect(r.thinking).toBe("field reasoning\n\ntag");
    expect(r.answer).toBe("final");
  });

  it("uses the reasoning field alone when content has no tags", () => {
    const r = parseThinking("plain answer", "my reasoning");
    expect(r.thinking).toBe("my reasoning");
    expect(r.answer).toBe("plain answer");
  });

  it("ignores an empty/whitespace reasoning field", () => {
    expect(parseThinking("answer", "   ").thinking).toBeNull();
    expect(parseThinking("answer", null).thinking).toBeNull();
  });

  it("trims surrounding whitespace from the answer", () => {
    expect(parseThinking("  <think>x</think>  hi  ").answer).toBe("hi");
  });

  it("returns empty answer and null thinking for empty input", () => {
    expect(parseThinking("")).toEqual({ thinking: null, answer: "" });
    expect(parseThinking("   ")).toEqual({ thinking: null, answer: "" });
  });
});
