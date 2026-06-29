import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { toast } from "sonner";
import { INFERENCE_URL } from "@/lib/api";
import { getToken } from "@/lib/tokenStore";
import { clearChat, loadChat, saveChat, type ChatMessage } from "@/lib/sandboxChatStore";

export interface UseSandboxChatOpts {
  /** Model name sent in the `model` field of /v1/chat/completions requests. */
  modelName?: string;
}

export interface UseSandboxChat {
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  clear: () => void;
  hydrated: boolean;
  /** True while a `send()` call is in flight. */
  isLoading: boolean;
  /**
   * Send a single-turn prompt via /v1/chat/completions with `x-sandbox: true`.
   * When `extraBody` is provided it is nested under the `"extra_body"` key in
   * the JSON body (raw-HTTP form understood by vLLM-Omni and compatible servers).
   * Handles both plain-text responses and vLLM-Omni image responses
   * (`choices[0].message.content[0].image_url.url`).
   * No-op when `opts.modelName` was not supplied to the hook.
   */
  send: (prompt: string, extraBody?: Record<string, unknown>) => Promise<void>;
}

/**
 * Owns the chat message list for a deployment: loads the stored thread on
 * mount / deployment change and persists every change.
 *
 * Loading uses React's "adjust state during render when a prop changes"
 * pattern (https://react.dev/reference/react/useState#storing-information-from-previous-renders):
 * when `deploymentId` flips, `messages` is reloaded synchronously *during
 * render*, before any effect runs. That is precisely what prevents the
 * stale-write clobber — by the time the persist effect fires, `messages`
 * already holds the NEW deployment's thread, so it can never write the previous
 * thread under the new key. (No effect-based hydration → no cascading-render
 * effect, and no need for a separate "skip first persist" guard.)
 */
export function useSandboxChat(deploymentId: string | null, opts?: UseSandboxChatOpts): UseSandboxChat {
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadChat(deploymentId));
  const [loadedFor, setLoadedFor] = useState<string | null>(deploymentId);
  const [isLoading, setIsLoading] = useState(false);
  // Ref so `send` can check loading state without stale closure.
  const isLoadingRef = useRef(false);

  // Reload synchronously during render when the deployment changes.
  if (deploymentId !== loadedFor) {
    setLoadedFor(deploymentId);
    setMessages(loadChat(deploymentId));
  }

  useEffect(() => {
    saveChat(deploymentId, messages);
  }, [messages, deploymentId]);

  const clear = useCallback(() => {
    setMessages([]);
    clearChat(deploymentId);
  }, [deploymentId]);

  const send = useCallback(async (prompt: string, extraBody?: Record<string, unknown>) => {
    if (!opts?.modelName || isLoadingRef.current) return;

    isLoadingRef.current = true;
    setIsLoading(true);

    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: prompt, timestamp: new Date() },
    ]);

    try {
      const base = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const body: Record<string, unknown> = {
        model: opts.modelName,
        messages: [{ role: "user", content: prompt }],
      };
      if (extraBody && Object.keys(extraBody).length > 0) {
        body.extra_body = extraBody;
      }

      const res = await fetch(`${base}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail || `API Error: ${res.status}`);
      }

      const data = await res.json() as {
        choices?: Array<{ message?: { content?: unknown } }>;
        usage?: { completion_tokens?: number };
      };
      const rawContent = data.choices?.[0]?.message?.content;

      let content = "";
      let imageUrl: string | undefined;

      if (Array.isArray(rawContent)) {
        // vLLM-Omni returns an array of typed content blocks.
        const imgBlock = rawContent.find(
          (c): c is { type: "image_url"; image_url: { url: string } } =>
            typeof c === "object" && c !== null && (c as { type?: string }).type === "image_url",
        );
        if (imgBlock) {
          imageUrl = imgBlock.image_url.url;
        } else {
          content = rawContent
            .map((c) => (typeof (c as { text?: unknown }).text === "string" ? (c as { text: string }).text : ""))
            .join("");
        }
      } else {
        content = typeof rawContent === "string" ? rawContent : "";
      }

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content,
          imageUrl,
          tokens: typeof data.usage?.completion_tokens === "number" ? data.usage.completion_tokens : undefined,
          timestamp: new Date(),
        },
      ]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate");
    } finally {
      isLoadingRef.current = false;
      setIsLoading(false);
    }
  }, [opts?.modelName]); // eslint-disable-line react-hooks/exhaustive-deps

  return { messages, setMessages, clear, hydrated: loadedFor === deploymentId, isLoading, send };
}
