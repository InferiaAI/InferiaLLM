import { useState, useRef, useEffect } from "react";
import { computeApi, INFERENCE_URL } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Send,
  Loader2,
  Copy,
  Check,
  Bot,
  User,
  Image,
  Video,
  Database,
  Play,
  Sparkles,
  AlertCircle,
  Settings2,
  Download,
  Wand2,
  FileImage,
  MessageSquare,
  RefreshCw,
  ChevronDown,
  Upload,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getToken } from "@/lib/tokenStore";

interface Deployment {
  id: string;
  name: string;
  modelName: string;
  model_type: string;
  engine?: string;
  endpointUrl: string;
  status: string;
}

interface DeploymentResponse {
  deployments: Array<{
    deployment_id: string;
    model_name?: string;
    engine?: string;
    endpoint?: string;
    state?: string;
    model_type?: string;
  }>;
}

type ModelCategory = "inference" | "embedding" | "image_generation" | "image_edit" | "video_generation" | "video_edit" | "video_extension";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

interface GenerationParams {
  temperature: number;
  max_tokens: number;
  top_p: number;
  stream: boolean;
  seed?: number;
  aspect_ratio?: string;
  negative_prompt?: string;
  controlnet?: string;
  num_images?: number;
}

const DEFAULT_PARAMS: Record<ModelCategory, GenerationParams> = {
  inference: { temperature: 0.7, max_tokens: 8192, top_p: 0.95, stream: false },
  embedding: { temperature: 0, max_tokens: 0, top_p: 0, stream: false },
  image_generation: { temperature: 0, max_tokens: 0, top_p: 0, stream: false, aspect_ratio: "1:1", negative_prompt: "", seed: 0, num_images: 1 },
  image_edit: { temperature: 0, max_tokens: 0, top_p: 0, stream: false, negative_prompt: "", seed: 0 },
  video_generation: { temperature: 0, max_tokens: 0, top_p: 0, stream: false, seed: 0 },
  video_edit: { temperature: 0, max_tokens: 0, top_p: 0, stream: false, seed: 0 },
  video_extension: { temperature: 0, max_tokens: 0, top_p: 0, stream: false, seed: 0 },
};

const ASPECT_RATIOS = [
  { value: "1:1", label: "1:1" },
  { value: "16:9", label: "16:9" },
  { value: "3:2", label: "3:2" },
  { value: "2:3", label: "2:3" },
  { value: "4:3", label: "4:3" },
  { value: "9:16", label: "9:16" },
  { value: "9:21", label: "9:21" },
];

const CONTROLNET_OPTIONS = [
  { value: "", label: "None" },
  { value: "canny", label: "Canny" },
  { value: "depth", label: "Depth" },
  { value: "pose", label: "Pose" },
  { value: "recolor", label: "Recolor" },
  { value: "sketch", label: "Sketch" },
  { value: "seg", label: "Segmentation" },
];

export default function Sandbox() {
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null);
  const [category, setCategory] = useState<ModelCategory>("inference");
  const [params, setParams] = useState<GenerationParams>(DEFAULT_PARAMS.inference);

  const { data: deployments = [], isLoading } = useQuery<Deployment[]>({
    queryKey: ["sandbox-deployments"],
    queryFn: async () => {
      const res = await computeApi.get<DeploymentResponse>("/deployment/deployments");
      return (res.data.deployments || [])
        .filter((d) => d.state === "READY" || d.state === "RUNNING")
        .map((d) => ({
          id: d.deployment_id,
          name: d.model_name || `Deployment-${(d.deployment_id || "").slice(0, 8)}`,
          modelName: d.model_name || "-",
          model_type: d.model_type || "inference",
          engine: d.engine,
          endpointUrl: d.endpoint || "",
          status: d.state || "UNKNOWN",
        }));
    },
    staleTime: 30000,
  });

  const selectedDeployment = deployments.find((d) => d.id === selectedDeploymentId) || deployments[0] || null;
  const effectiveCategory = selectedDeployment
    ? getCategoryFromModelType(selectedDeployment.model_type, selectedDeployment.engine)
    : category;

  const handleDeploymentChange = (deployment: Deployment) => {
    setSelectedDeploymentId(deployment.id);
    const cat = getCategoryFromModelType(deployment.model_type, deployment.engine);
    setCategory(cat);
    setParams(DEFAULT_PARAMS[cat]);
  };

  const updateParam = <K extends keyof GenerationParams>(key: K, value: GenerationParams[K]) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Playground</h1>
            <p className="text-sm text-muted-foreground">
              Test your deployed models interactively
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {deployments.filter((d) => d.status === "READY" || d.status === "RUNNING").length} deployments ready
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-4 xl:col-span-3 space-y-4">
          <div className="rounded-xl border bg-card shadow-sm">
            <div className="p-3 border-b bg-muted/30">
              <h3 className="text-sm font-medium flex items-center gap-2">
                <Settings2 className="w-4 h-4" />
                Configuration
              </h3>
            </div>
            <div className="p-3 space-y-4">
              <div>
                <label className="text-xs font-medium mb-1.5 block">Model</label>
                {isLoading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Loading...
                  </div>
                ) : deployments.length === 0 ? (
                  <div className="text-sm text-muted-foreground flex items-center gap-2">
                    <AlertCircle className="w-4 h-4" />
                    No deployments
                  </div>
                ) : (
                  <select
                    value={selectedDeploymentId || ""}
                    onChange={(e) => {
                      const dep = deployments.find((d) => d.id === e.target.value);
                      if (dep) handleDeploymentChange(dep);
                    }}
                    className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:ring-1 focus:ring-emerald-500 outline-none"
                  >
                    {deployments.map((dep) => (
                      <option key={dep.id} value={dep.id}>
                        {dep.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              {effectiveCategory === "inference" && (
                <>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Temperature</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.01"
                        value={params.temperature}
                        onChange={(e) => updateParam("temperature", parseFloat(e.target.value))}
                        className="flex-1"
                      />
                      <span className="text-xs w-10 text-right">{params.temperature.toFixed(2)}</span>
                    </div>
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Max Tokens</label>
                    <input
                      type="number"
                      value={params.max_tokens}
                      onChange={(e) => updateParam("max_tokens", parseInt(e.target.value) || 0)}
                      className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                      placeholder="8192"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Top P</label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={params.top_p}
                        onChange={(e) => updateParam("top_p", parseFloat(e.target.value))}
                        className="flex-1"
                      />
                      <span className="text-xs w-10 text-right">{params.top_p.toFixed(2)}</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium">Stream</label>
                    <button
                      onClick={() => updateParam("stream", !params.stream)}
                      className={cn(
                        "w-10 h-5 rounded-full transition-colors relative",
                        params.stream ? "bg-emerald-500" : "bg-muted"
                      )}
                    >
                      <span
                        className={cn(
                          "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
                          params.stream ? "left-5" : "left-0.5"
                        )}
                      />
                    </button>
                  </div>
                </>
              )}

              {effectiveCategory === "image_generation" && (
                <>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Aspect Ratio</label>
                    <select
                      value={params.aspect_ratio || "1:1"}
                      onChange={(e) => updateParam("aspect_ratio", e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                    >
                      {ASPECT_RATIOS.map((ratio) => (
                        <option key={ratio.value} value={ratio.value}>
                          {ratio.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Negative Prompt</label>
                    <textarea
                      value={params.negative_prompt || ""}
                      onChange={(e) => updateParam("negative_prompt", e.target.value)}
                      placeholder="What to avoid..."
                      className="w-full h-20 px-3 py-2 rounded-lg border bg-background text-sm resize-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Seed (optional)</label>
                    <input
                      type="number"
                      value={params.seed || ""}
                      onChange={(e) => updateParam("seed", parseInt(e.target.value) || 0)}
                      className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                      placeholder="Random"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">ControlNet</label>
                    <select
                      value={params.controlnet || ""}
                      onChange={(e) => updateParam("controlnet", e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                    >
                      {CONTROLNET_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium mb-1.5 block">Number of Images</label>
                    <input
                      type="number"
                      min="1"
                      max="4"
                      value={params.num_images || 1}
                      onChange={(e) => updateParam("num_images", parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                    />
                  </div>
                </>
              )}

              {(effectiveCategory === "video_generation" || effectiveCategory === "video_edit" || effectiveCategory === "video_extension") && (
                <div>
                  <label className="text-xs font-medium mb-1.5 block">Seed (optional)</label>
                  <input
                    type="number"
                    value={params.seed || ""}
                    onChange={(e) => updateParam("seed", parseInt(e.target.value) || 0)}
                    className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
                    placeholder="Random"
                  />
                </div>
              )}

              {effectiveCategory === "embedding" && (
                <div className="text-sm text-muted-foreground text-center py-4">
                  <Database className="w-8 h-8 mx-auto mb-2 opacity-50" />
                  No additional parameters for embeddings
                </div>
              )}
            </div>
          </div>

          <div className="rounded-xl border bg-card shadow-sm">
            <div className="p-3 border-b bg-muted/30">
              <h3 className="text-sm font-medium">Model Info</h3>
            </div>
            <div className="p-3 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Engine</span>
                <span className="font-mono">{selectedDeployment?.engine || "-"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Type</span>
                <span className="capitalize">{category.replace("_", " ")}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Status</span>
                <span className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  {selectedDeployment?.status || "N/A"}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="lg:col-span-8 xl:col-span-9">
          {!selectedDeployment ? (
            <div className="rounded-xl border bg-card p-12 shadow-sm text-center">
              <Sparkles className="w-16 h-16 mx-auto text-muted-foreground/20 mb-4" />
              <h3 className="text-lg font-medium mb-2">Select a Deployment</h3>
              <p className="text-sm text-muted-foreground">
                Choose a ready deployment from the left to start testing
              </p>
            </div>
          ) : (
            <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
              <div className="border-b p-3 bg-muted/30 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {effectiveCategory === "inference" && <MessageSquare className="w-4 h-4" />}
                  {effectiveCategory === "embedding" && <Database className="w-4 h-4" />}
                  {effectiveCategory === "image_generation" && <Image className="w-4 h-4" />}
                  {effectiveCategory === "image_edit" && <Wand2 className="w-4 h-4" />}
                  {(effectiveCategory === "video_generation" || effectiveCategory === "video_edit" || effectiveCategory === "video_extension") && <Video className="w-4 h-4" />}
                  <span className="font-medium text-sm capitalize">{category.replace("_", " ")}</span>
                </div>
                <span className="text-xs text-muted-foreground font-mono">{selectedDeployment.modelName}</span>
              </div>

              <div className="min-h-[500px]">
                {effectiveCategory === "inference" && <ChatInterface deployment={selectedDeployment} params={params} />}
                {effectiveCategory === "embedding" && <EmbeddingInterface deployment={selectedDeployment} />}
                {effectiveCategory === "image_generation" && <ImageGenerationInterface deployment={selectedDeployment} params={params} />}
                {effectiveCategory === "image_edit" && <ImageEditInterface deployment={selectedDeployment} />}
                {effectiveCategory === "video_generation" && <VideoGenerationInterface deployment={selectedDeployment} />}
                {effectiveCategory === "video_edit" && <VideoEditInterface deployment={selectedDeployment} />}
                {effectiveCategory === "video_extension" && <VideoExtensionInterface deployment={selectedDeployment} />}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ChatInterface({ deployment, params }: { deployment: Deployment; params: GenerationParams }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const fullMessages = systemPrompt.trim()
      ? [{ role: "system" as const, content: systemPrompt.trim() }, ...messages, { role: "user" as const, content: input.trim() }]
      : [...messages, { role: "user" as const, content: input.trim() }];

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

      const requestBody: Record<string, unknown> = {
        model: deployment.modelName,
        messages: fullMessages,
        stream: params.stream,
      };

      if (params.temperature > 0) requestBody.temperature = params.temperature;
      if (params.max_tokens > 0) requestBody.max_tokens = params.max_tokens;
      if (params.top_p > 0) requestBody.top_p = params.top_p;

      const response = await fetch(`${inferenceBaseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      if (params.stream) {
        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        let assistantContent = "";

        const assistantMessage: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, assistantMessage]);

        if (reader) {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value);
            const lines = chunk.split("\n");
            for (const line of lines) {
              if (line.startsWith("data: ")) {
                const data = line.slice(6);
                if (data === "[DONE]") continue;
                try {
                  const parsed = JSON.parse(data);
                  const delta = parsed.choices?.[0]?.delta?.content || "";
                  assistantContent += delta;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMessage.id ? { ...m, content: assistantContent } : m
                    )
                  );
                } catch {
                  // Ignore parsing errors for streaming
                }
              }
            }
          }
        }
      } else {
        const data = await response.json();
        const assistantMessage: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: data.choices?.[0]?.message?.content || "No response generated",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, assistantMessage]);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to generate response");
      const errorMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "Error: Failed to get response from the model.",
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="border-b">
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="w-full px-4 py-2 text-left text-sm flex items-center justify-between hover:bg-muted/50"
        >
          <span className="flex items-center gap-2">
            <Settings2 className="w-4 h-4" />
            System Prompt
          </span>
          <ChevronDown className={cn("w-4 h-4 transition-transform", showSettings && "rotate-180")} />
        </button>
        {showSettings && (
          <div className="px-4 pb-3">
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Optional system prompt..."
              className="w-full h-20 px-3 py-2 rounded-lg border bg-background text-sm resize-none"
            />
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <MessageSquare className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Send a message to start the conversation</p>
          </div>
        ) : (
          messages.map((message) => (
            <ChatMessageItem key={message.id} message={message} />
          ))
        )}
        {isLoading && !params.stream && (
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
            className="flex-1 px-4 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
      </form>
    </div>
  );
}

function ChatMessageItem({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={cn("flex gap-3", message.role === "user" ? "flex-row-reverse" : "flex-row")}>
      <div
        className={cn(
          "w-8 h-8 rounded-full flex items-center justify-center shrink-0",
          message.role === "user" ? "bg-emerald-100 dark:bg-emerald-900/30" : "bg-slate-100 dark:bg-slate-800"
        )}
      >
        {message.role === "user" ? (
          <User className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <Bot className="w-4 h-4 text-slate-600 dark:text-slate-400" />
        )}
      </div>
      <div
        className={cn(
          "flex-1 max-w-[85%] rounded-lg p-3",
          message.role === "user"
            ? "bg-emerald-500/10 border border-emerald-500/20"
            : "bg-muted border border-border"
        )}
      >
        <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        <div className="flex justify-end mt-2">
          <button onClick={handleCopy} className="p-1 hover:bg-accent rounded transition-colors" title="Copy">
            {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3 text-muted-foreground" />}
          </button>
        </div>
      </div>
    </div>
  );
}

function EmbeddingInterface({ deployment }: { deployment: Deployment }) {
  const [input, setInput] = useState("");
  const [embeddings, setEmbeddings] = useState<number[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleGenerate = async () => {
    if (!input.trim() || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/embeddings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          input: input.trim(),
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      const embedding = data.data?.[0]?.embedding;
      if (embedding) {
        setEmbeddings(embedding);
      } else {
        throw new Error("No embedding returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to generate embeddings");
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopy = () => {
    if (embeddings) {
      navigator.clipboard.writeText(JSON.stringify(embeddings));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Enter text to generate embeddings..."
          className="w-full h-32 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
          disabled={isLoading}
        />
        <div className="flex justify-between items-center mt-3">
          <button
            onClick={handleGenerate}
            disabled={!input.trim() || isLoading}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center gap-2"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Compute Embeddings
          </button>
          {embeddings && (
            <span className="text-xs text-muted-foreground">
              Dimensions: {embeddings.length}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {embeddings ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium">Embedding Vector</h4>
              <button
                onClick={handleCopy}
                className="p-1.5 hover:bg-accent rounded transition-colors inline-flex items-center gap-1 text-xs"
              >
                {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
            <div className="p-3 bg-muted rounded-lg border font-mono text-xs overflow-x-auto max-h-64">
              [{embeddings.slice(0, 50).map((v) => v.toFixed(6)).join(", ")}
              {embeddings.length > 50 && (
                <span className="text-muted-foreground"> ... +{embeddings.length - 50} more</span>
              )}
              ]
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Database className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Enter text above to generate embeddings</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ImageGenerationInterface({ deployment, params }: { deployment: Deployment; params: GenerationParams }) {
  const [prompt, setPrompt] = useState("");
  const [negativePrompt] = useState(params.negative_prompt || "");
  const [images, setImages] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const handleGenerate = async () => {
    if (!prompt.trim() || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/images/generations`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
          negative_prompt: negativePrompt.trim() || undefined,
          aspect_ratio: params.aspect_ratio,
          seed: params.seed || undefined,
          controlnet: params.controlnet || undefined,
          num_images: params.num_images || 1,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      const generatedImages = data.data?.map((img: { url?: string; b64_json?: string }) => {
        if (img.url) return img.url;
        if (img.b64_json) return `data:image/png;base64,${img.b64_json}`;
        return null;
      }).filter(Boolean);

      if (generatedImages && generatedImages.length > 0) {
        setImages(generatedImages);
      } else {
        throw new Error("No images returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to generate image");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDownload = (url: string, idx: number) => {
    const link = document.createElement("a");
    link.href = url;
    link.download = `generated-${idx + 1}.png`;
    link.click();
  };

  const handleCopy = async (url: string) => {
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      toast.success("Copied to clipboard!");
    } catch {
      toast.error("Failed to copy");
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b space-y-3">
        <div>
          <label className="text-xs font-medium mb-1 block">Prompt</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe the image you want to generate..."
            className="w-full h-20 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
            disabled={isLoading}
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleGenerate}
            disabled={!prompt.trim() || isLoading}
            className="flex-1 px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-2"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            Generate
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-2 gap-4">
            {images.map((img, idx) => (
              <div key={idx} className="relative group">
                <img
                  src={img}
                  alt={`Generated ${idx + 1}`}
                  className="rounded-lg border w-full"
                />
                <div className="absolute bottom-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => handleDownload(img, idx)}
                    className="p-1.5 bg-black/50 rounded text-white hover:bg-black/70"
                    title="Download"
                  >
                    <Download className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleCopy(img)}
                    className="p-1.5 bg-black/50 rounded text-white hover:bg-black/70"
                    title="Copy"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <FileImage className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Enter a prompt to generate an image</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ImageEditInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [mode, setMode] = useState<"edit" | "variation">("edit");

  const handleGenerate = async () => {
    if ((!prompt.trim() && !image.trim()) || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/images/edits`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
          image: image.trim() || undefined,
          n: 1,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      const generatedImages = data.data?.map((img: { url?: string; b64_json?: string }) => {
        if (img.url) return img.url;
        if (img.b64_json) return `data:image/png;base64,${img.b64_json}`;
        return null;
      }).filter(Boolean);

      if (generatedImages && generatedImages.length > 0) {
        setImages(generatedImages);
      } else {
        throw new Error("No images returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to edit image");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b space-y-3">
        <div className="flex gap-2">
          <button
            onClick={() => setMode("edit")}
            className={cn(
              "px-3 py-1.5 rounded text-xs font-medium",
              mode === "edit" ? "bg-emerald-600 text-white" : "bg-muted"
            )}
          >
            Edit
          </button>
          <button
            onClick={() => setMode("variation")}
            className={cn(
              "px-3 py-1.5 rounded text-xs font-medium",
              mode === "variation" ? "bg-emerald-600 text-white" : "bg-muted"
            )}
          >
            Variation
          </button>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Upload Image</label>
          <div className="border-2 border-dashed rounded-lg p-6 text-center">
            <input
              type="file"
              accept="image/*"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) {
                  const reader = new FileReader();
                  reader.onload = () => {
                    const base64 = (reader.result as string).split(",")[1];
                    setImage(base64);
                  };
                  reader.readAsDataURL(file);
                }
              }}
              className="hidden"
              id="image-upload"
            />
            <label htmlFor="image-upload" className="cursor-pointer">
              <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
              <p className="text-xs text-muted-foreground">
                {image ? "Image loaded" : "Click to upload image"}
              </p>
            </label>
          </div>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Prompt {mode === "edit" && "(optional)"}</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={mode === "edit" ? "Describe the edit..." : "Describe the variation..."}
            className="w-full h-16 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
            disabled={isLoading}
          />
        </div>
        <button
          onClick={handleGenerate}
          disabled={!image || isLoading}
          className="w-full px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-2"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
          {mode === "edit" ? "Edit Image" : "Create Variation"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-2 gap-4">
            {images.map((img, idx) => (
              <img
                key={idx}
                src={img}
                alt={`Edited ${idx + 1}`}
                className="rounded-lg border"
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Wand2 className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Upload an image to edit</p>
          </div>
        )}
      </div>
    </div>
  );
}

function VideoGenerationInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleGenerate = async () => {
    if (!prompt.trim() || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/generations`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      if (data.url || data.video_url) {
        setVideoUrl(data.url || data.video_url);
      } else {
        throw new Error("No video returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to generate video");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b space-y-3">
        <div>
          <label className="text-xs font-medium mb-1 block">Prompt</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe the video you want to generate..."
            className="w-full h-24 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
            disabled={isLoading}
          />
        </div>
        <button
          onClick={handleGenerate}
          disabled={!prompt.trim() || isLoading}
          className="w-full px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-2"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Video className="w-4 h-4" />}
          Generate Video
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video
            src={videoUrl}
            controls
            className="rounded-lg border w-full max-h-80 mx-auto"
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Video className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Enter a prompt to generate a video</p>
          </div>
        )}
      </div>
    </div>
  );
}

function VideoEditInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [video, setVideo] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleGenerate = async () => {
    if (!video.trim() || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/edits`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim() || undefined,
          video: video.trim(),
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      if (data.url || data.video_url) {
        setVideoUrl(data.url || data.video_url);
      } else {
        throw new Error("No video returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to edit video");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b space-y-3">
        <div>
          <label className="text-xs font-medium mb-1 block">Upload Video</label>
          <div className="border-2 border-dashed rounded-lg p-6 text-center">
            <input
              type="file"
              accept="video/*"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) {
                  const reader = new FileReader();
                  reader.onload = () => {
                    const base64 = (reader.result as string).split(",")[1];
                    setVideo(base64);
                  };
                  reader.readAsDataURL(file);
                }
              }}
              className="hidden"
              id="video-upload"
            />
            <label htmlFor="video-upload" className="cursor-pointer">
              <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
              <p className="text-xs text-muted-foreground">
                {video ? "Video loaded" : "Click to upload video"}
              </p>
            </label>
          </div>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Prompt (optional)</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe how to edit the video..."
            className="w-full h-16 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
            disabled={isLoading}
          />
        </div>
        <button
          onClick={handleGenerate}
          disabled={!video || isLoading}
          className="w-full px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-2"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
          Edit Video
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video
            src={videoUrl}
            controls
            className="rounded-lg border w-full max-h-80 mx-auto"
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Wand2 className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Upload a video to edit</p>
          </div>
        )}
      </div>
    </div>
  );
}

function VideoExtensionInterface({ deployment }: { deployment: Deployment }) {
  const [video, setVideo] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleGenerate = async () => {
    if (!video.trim() || isLoading) return;

    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/extensions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          video: video.trim(),
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      if (data.url || data.video_url) {
        setVideoUrl(data.url || data.video_url);
      } else {
        throw new Error("No video returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to extend video");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[500px]">
      <div className="p-4 border-b space-y-3">
        <div>
          <label className="text-xs font-medium mb-1 block">Upload Video to Extend</label>
          <div className="border-2 border-dashed rounded-lg p-6 text-center">
            <input
              type="file"
              accept="video/*"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) {
                  const reader = new FileReader();
                  reader.onload = () => {
                    const base64 = (reader.result as string).split(",")[1];
                    setVideo(base64);
                  };
                  reader.readAsDataURL(file);
                }
              }}
              className="hidden"
              id="video-extend-upload"
            />
            <label htmlFor="video-extend-upload" className="cursor-pointer">
              <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
              <p className="text-xs text-muted-foreground">
                {video ? "Video loaded" : "Click to upload video"}
              </p>
            </label>
          </div>
        </div>
        <button
          onClick={handleGenerate}
          disabled={!video || isLoading}
          className="w-full px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-2"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Extend Video
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video
            src={videoUrl}
            controls
            className="rounded-lg border w-full max-h-80 mx-auto"
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <RefreshCw className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Upload a video to extend</p>
          </div>
        )}
      </div>
    </div>
  );
}

function getCategoryFromModelType(modelType?: string, engine?: string): ModelCategory {
  if (modelType === "embedding" || engine === "infinity" || engine === "tei") {
    return "embedding";
  }
  if (modelType === "image_generation" || engine === "inferia-diffusion") {
    return "image_generation";
  }
  if (modelType === "video_generation") {
    return "video_generation";
  }
  return "inference";
}
