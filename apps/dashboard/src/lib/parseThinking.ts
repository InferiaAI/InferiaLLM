/**
 * Split a raw assistant message into reasoning ("thinking") and the final
 * answer. Reasoning models emit `<think>…</think>` inline and/or a separate
 * `reasoning_content` field. Tolerant of multiple and unclosed blocks so a
 * truncated/streamed response still parses.
 */

export interface ParsedThinking {
  thinking: string | null;
  answer: string;
}

const CLOSED_BLOCK = /<think>([\s\S]*?)<\/think>/gi;
const OPEN_TRAILING = /<think>([\s\S]*)$/i;
const STRAY_TAGS = /<\/?think>/gi;

/** Remove any literal <think>/</think> tags so nested/interleaved blocks never
 *  leak a raw tag into the displayed thinking or answer. */
function stripTags(s: string): string {
  return s.replace(STRAY_TAGS, "");
}

export function parseThinking(content: string, reasoning?: string | null): ParsedThinking {
  const text = typeof content === "string" ? content : "";
  const parts: string[] = [];

  // 1) Remove all fully-closed <think>…</think> blocks.
  let answer = text.replace(CLOSED_BLOCK, (_match, inner: string) => {
    parts.push(stripTags(inner).trim());
    return "";
  });

  // 2) A trailing, unclosed <think> (truncated / mid-stream) → all thinking.
  const open = answer.match(OPEN_TRAILING);
  if (open && open.index !== undefined) {
    parts.push(stripTags(open[1]).trim());
    answer = answer.slice(0, open.index);
  }

  // 3) An explicit reasoning field wins — prepend it.
  const field = typeof reasoning === "string" ? reasoning.trim() : "";
  if (field) parts.unshift(field);

  const thinking = parts.filter((p) => p.length > 0).join("\n\n");
  return {
    thinking: thinking.length > 0 ? thinking : null,
    // Strip any stray tags left behind by nested/interleaved blocks.
    answer: stripTags(answer).trim(),
  };
}
