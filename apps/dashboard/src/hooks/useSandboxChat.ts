import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
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
 * pattern (https://react.dev/reference/react/useState#storing-information-from-previous-renders)
 * rather than an effect — so `messages` is already correct on the render where
 * `deploymentId` flips, avoiding both a cascading-render effect and the
 * stale-write race. The persist effect additionally skips its first run after
 * a deployment change (the `prevDeployment` ref) so the previous thread is
 * never written under the new key.
 */
export function useSandboxChat(deploymentId: string | null): UseSandboxChat {
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadChat(deploymentId));
  const [loadedFor, setLoadedFor] = useState<string | null>(deploymentId);
  const prevDeployment = useRef<string | null>(deploymentId);

  // Reload synchronously during render when the deployment changes.
  if (deploymentId !== loadedFor) {
    setLoadedFor(deploymentId);
    setMessages(loadChat(deploymentId));
  }

  useEffect(() => {
    // First run after a deployment change carries stale messages — skip it.
    if (prevDeployment.current !== deploymentId) {
      prevDeployment.current = deploymentId;
      return;
    }
    saveChat(deploymentId, messages);
  }, [messages, deploymentId]);

  const clear = useCallback(() => {
    setMessages([]);
    clearChat(deploymentId);
  }, [deploymentId]);

  return { messages, setMessages, clear, hydrated: loadedFor === deploymentId };
}
