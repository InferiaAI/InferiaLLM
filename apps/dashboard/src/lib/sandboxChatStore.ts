/**
 * Sandbox chat persistence — one conversation per deployment, stored in
 * localStorage. Pure functions; every localStorage access is guarded so a
 * private-mode / quota / corrupt-data situation degrades to an empty thread
 * instead of throwing.
 */

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Optional separate reasoning_content returned by some engines. */
  reasoning?: string;
  /** Optional completion token count (from usage), shown in the footer. */
  tokens?: number;
  timestamp: Date;
}

interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  tokens?: number;
  timestamp: number; // epoch ms
}

interface StoredThread {
  v: number;
  messages: StoredMessage[];
}

const SCHEMA_VERSION = 1;
const KEY_PREFIX = "inferia.sandbox.chat.v1::";

/** Cap stored messages to bound localStorage usage. */
export const MAX_MESSAGES = 200;

function keyFor(deploymentId: string): string {
  return `${KEY_PREFIX}${deploymentId}`;
}

export function loadChat(deploymentId: string | null | undefined): ChatMessage[] {
  if (!deploymentId) return [];
  let raw: string | null;
  try {
    raw = localStorage.getItem(keyFor(deploymentId));
  } catch {
    return [];
  }
  if (!raw) return [];

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }

  const thread = parsed as StoredThread | null;
  if (
    !thread ||
    typeof thread !== "object" ||
    thread.v !== SCHEMA_VERSION ||
    !Array.isArray(thread.messages)
  ) {
    return [];
  }

  return thread.messages
    .filter(
      (m): m is StoredMessage =>
        !!m &&
        typeof m === "object" &&
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string",
    )
    .map((m) => ({
      id: typeof m.id === "string" ? m.id : crypto.randomUUID(),
      role: m.role,
      content: m.content,
      reasoning: typeof m.reasoning === "string" ? m.reasoning : undefined,
      tokens: typeof m.tokens === "number" ? m.tokens : undefined,
      timestamp: new Date(typeof m.timestamp === "number" ? m.timestamp : 0),
    }));
}

export function saveChat(
  deploymentId: string | null | undefined,
  messages: ChatMessage[],
): void {
  if (!deploymentId) return;
  const payload: StoredThread = {
    v: SCHEMA_VERSION,
    messages: messages.slice(-MAX_MESSAGES).map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      reasoning: m.reasoning,
      tokens: m.tokens,
      timestamp: m.timestamp instanceof Date ? m.timestamp.getTime() : Number(m.timestamp) || 0,
    })),
  };
  try {
    localStorage.setItem(keyFor(deploymentId), JSON.stringify(payload));
  } catch {
    // quota exceeded / storage unavailable — keep the in-memory copy only.
  }
}

export function clearChat(deploymentId: string | null | undefined): void {
  if (!deploymentId) return;
  try {
    localStorage.removeItem(keyFor(deploymentId));
  } catch {
    // ignore
  }
}
