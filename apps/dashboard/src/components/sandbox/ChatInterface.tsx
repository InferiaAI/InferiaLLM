import { useState, useRef, useEffect } from "react";
import { toast } from "sonner";
import {
  Send,
  Loader2,
  Settings2,
  ChevronDown,
  MessageSquare,
  Trash2,
  User,
  Bot,
  Copy,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { INFERENCE_URL } from "@/lib/api";
import { getToken } from "@/lib/tokenStore";
import { useSandboxChat } from "@/hooks/useSandboxChat";
import { parseThinking } from "@/lib/parseThinking";
import { MarkdownMessage } from "./MarkdownMessage";
import { ThinkingBlock } from "./ThinkingBlock";
import type { ChatMessage } from "@/lib/sandboxChatStore";

/** Minimal shape the chat needs from a deployment (structurally compatible
 *  with the page's richer Deployment type). */
export interface ChatDeployment {
  id: string;
  modelName: string;
}

export function ChatInterface({ deployment }: { deployment: ChatDeployment }) {
  const { messages, setMessages, clear } = useSandboxChat(deployment.id);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Reset the inline clear-confirm whenever the conversation or model changes.
  useEffect(() => setConfirmClear(false), [deployment.id, messages.length]);

  const handleClear = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    clear();
    setConfirmClear(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    // Re-send only the final answer for prior assistant turns — never the raw
    // <think> scratchpad (it degrades multi-turn quality / breaks reasoning
    // chat templates).
    const history = messages.map((m) => ({
      role: m.role,
      content: m.role === "assistant" ? parseThinking(m.content, m.reasoning).answer : m.content,
    }));
    const fullMessages = systemPrompt.trim()
      ? [{ role: "system" as const, content: systemPrompt.trim() }, ...history, { role: "user" as const, content: input.trim() }]
      : [...history, { role: "user" as const, content: input.trim() }];

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({ model: deployment.modelName, messages: fullMessages }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      const choice = data.choices?.[0];
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        // Store the raw content as-is; the empty/placeholder decision is made at
        // render time so a reasoning-only response isn't mislabelled.
        content: choice?.message?.content ?? "",
        reasoning: choice?.message?.reasoning_content || undefined,
        tokens: typeof data.usage?.completion_tokens === "number" ? data.usage.completion_tokens : undefined,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to generate response");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b">
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="flex flex-1 items-center justify-between px-4 py-2 text-left text-sm hover:bg-muted/50"
        >
          <span className="flex items-center gap-2">
            <Settings2 className="w-4 h-4" />
            System Prompt
          </span>
          <ChevronDown className={cn("w-4 h-4 transition-transform", showSettings && "rotate-180")} />
        </button>
        <button
          onClick={handleClear}
          disabled={messages.length === 0 && !confirmClear}
          className={cn(
            "mx-2 flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-40",
            confirmClear
              ? "bg-red-500/15 text-red-600 hover:bg-red-500/25"
              : "text-muted-foreground hover:bg-muted"
          )}
        >
          <Trash2 className="h-3.5 w-3.5" />
          {confirmClear ? "Confirm?" : "Clear"}
        </button>
      </div>
      {showSettings && (
        <div className="border-b px-4 pb-3 pt-2">
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Optional system prompt..."
            className="h-20 w-full resize-none rounded-lg border bg-background px-3 py-2 text-sm"
          />
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <MessageSquare className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Send a message to start the conversation</p>
          </div>
        ) : (
          messages.map((message) => <ChatMessageItem key={message.id} message={message} />)
        )}
        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" />
            Generating...
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="border-t p-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type your message..."
            className="flex-1 px-4 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-ember-500 outline-none"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            aria-label="Send"
            className="px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50 transition-colors"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
      </form>
    </div>
  );
}

export function ChatMessageItem({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const parsed = isUser ? null : parseThinking(message.content, message.reasoning);
  const answer = parsed?.answer ?? "";
  const hasThinking = !!parsed?.thinking;
  // A thinking-only turn shows just the disclosure — no empty answer bubble.
  const showBubble = isUser || answer.length > 0 || !hasThinking;

  const handleCopy = () => {
    void navigator.clipboard?.writeText(isUser ? message.content : (answer || message.content));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={cn("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-ember-100 dark:bg-ember-900/30" : "bg-muted dark:bg-card"
        )}
      >
        {isUser ? <User className="w-4 h-4 text-ember-600" /> : <Bot className="w-4 h-4 text-muted-foreground" />}
      </div>
      <div
        className={cn(
          "max-w-[85%] flex-1",
          showBubble && "rounded-lg p-3",
          showBubble && (isUser ? "border border-ember-500/20 bg-ember-500/10" : "border border-border bg-muted")
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{message.content}</p>
        ) : (
          <>
            {parsed?.thinking && <ThinkingBlock thinking={parsed.thinking} />}
            {answer ? (
              <MarkdownMessage content={answer} />
            ) : !hasThinking ? (
              <p className="text-sm italic text-muted-foreground">No response generated</p>
            ) : null}
          </>
        )}
        {showBubble && (
          <div className="mt-2 flex items-center justify-end gap-2">
            {!isUser && typeof message.tokens === "number" && (
              <span className="text-[10px] text-muted-foreground">{message.tokens} tok</span>
            )}
            <button onClick={handleCopy} aria-label="Copy message" className="rounded p-1 hover:bg-accent">
              {copied ? <Check className="w-3 h-3 text-ember-500" /> : <Copy className="w-3 h-3 text-muted-foreground" />}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
