import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { clearChat, loadChat, saveChat, type ChatMessage } from "@/lib/sandboxChatStore";

export interface UseSandboxChat {
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  clear: () => void;
  hydrated: boolean;
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
export function useSandboxChat(deploymentId: string | null): UseSandboxChat {
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadChat(deploymentId));
  const [loadedFor, setLoadedFor] = useState<string | null>(deploymentId);

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

  return { messages, setMessages, clear, hydrated: loadedFor === deploymentId };
}
