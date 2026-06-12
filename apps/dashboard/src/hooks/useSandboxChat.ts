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
 * mount / deployment change and persists every change. Guards against the
 * classic hydration race — when the deployment id flips, the persist effect
 * sees the *old* messages in its closure; the `prevDeployment` ref skips that
 * one run so we never write the previous thread under the new key.
 */
export function useSandboxChat(deploymentId: string | null): UseSandboxChat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const prevDeployment = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    setHydrated(false);
    setMessages(loadChat(deploymentId));
    setHydrated(true);
  }, [deploymentId]);

  useEffect(() => {
    // First run after a deployment change carries stale messages — skip it.
    if (prevDeployment.current !== deploymentId) {
      prevDeployment.current = deploymentId;
      return;
    }
    if (!hydrated) return;
    saveChat(deploymentId, messages);
  }, [messages, hydrated, deploymentId]);

  const clear = useCallback(() => {
    setMessages([]);
    clearChat(deploymentId);
  }, [deploymentId]);

  return { messages, setMessages, clear, hydrated };
}
