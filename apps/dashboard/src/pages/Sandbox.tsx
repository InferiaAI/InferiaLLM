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
  Video,
  Database,
  Sparkles,
  AlertCircle,
  Settings2,
  Wand2,
  FileImage,
  MessageSquare,
  RefreshCw,
  ChevronDown,
  Upload,
  Layers,
  Maximize2,
  Hash,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getToken } from "@/lib/tokenStore";

interface Deployment {
  id: string;
  name: string;
  modelName: string;
  model_type: string;
  engine?: string;
  workload_type?: string;
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
    configuration?: {
      workload_type?: string;
    };
  }>;
}

type ModelCategory = "inference" | "embedding" | "image" | "video";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

interface ImageParams {
  prompt: string;
  n: number;
  size: string;
  response_format: string;
  quality?: string;
  style?: string;
  scheduler?: string;
  negative_prompt?: string;
  seed?: number;
}

interface VideoGenParams {
  prompt: string;
  input_reference?: string;
  size: string;
  seconds: number;
  n: number;
  response_format: string;
}

interface ChatParams {
  temperature: number;
  max_tokens: number;
  top_p: number;
  stream: boolean;
}

const IMAGE_SIZES = [
  { value: "512x512", label: "512x512" },
  { value: "768x768", label: "768x768" },
  { value: "1024x1024", label: "1024x1024" },
  { value: "1024x576", label: "1024x576 (16:9)" },
  { value: "576x1024", label: "576x1024 (9:16)" },
];

const VIDEO_SIZES = [
  { value: "720x1280", label: "720x1280 (9:16)" },
  { value: "1280x720", label: "1280x720 (16:9)" },
  { value: "1024x1024", label: "1024x1024" },
];

const SCHEDULERS = [
  { value: "", label: "Default" },
  { value: "EulerDiscreteScheduler", label: "Euler" },
  { value: "DPM++ 2M", label: "DPM++ 2M" },
  { value: "UniPCMultistepScheduler", label: "UniPC" },
  { value: "DDIMScheduler", label: "DDIM" },
];

const STYLES = [
  { value: "", label: "None" },
  { value: "natural", label: "Natural" },
  { value: "vivid", label: "Vivid" },
  { value: "anime", label: "Anime" },
  { value: "photorealistic", label: "Photorealistic" },
];

export default function Sandbox() {
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null);

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
          workload_type: d.configuration?.workload_type,
          endpointUrl: d.endpoint || "",
          status: d.state || "UNKNOWN",
        }));
    },
    staleTime: 30000,
  });

  const { data: deploymentDetails } = useQuery({
    queryKey: ["deployment-details", selectedDeploymentId],
    queryFn: async () => {
      if (!selectedDeploymentId) return null;
      const { data } = await computeApi.get(`/deployment/status/${selectedDeploymentId}`);
      return data;
    },
    enabled: !!selectedDeploymentId,
  });

  const selectedDeployment = deployments.find((d) => d.id === selectedDeploymentId) || deployments[0] || null;
  const effectiveCategory = selectedDeployment ? getCategoryFromModelType(selectedDeployment.model_type, selectedDeployment.engine, deploymentDetails?.configuration?.workload_type) : "inference";

  const handleDeploymentChange = (deployment: Deployment) => {
    setSelectedDeploymentId(deployment.id);
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
        <div className="lg:col-span-3 space-y-4">
          <div className="rounded-xl border bg-card shadow-sm">
            <div className="p-3 border-b bg-muted/30">
              <h3 className="text-sm font-medium flex items-center gap-2">
                <Settings2 className="w-4 h-4" />
                Model
              </h3>
            </div>
            <div className="p-3">
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
                  className="w-full px-3 py-2 rounded-lg border bg-background text-sm focus:ring-1 focus:ring-ember-500 outline-none"
                >
                  {deployments.map((dep) => (
                    <option key={dep.id} value={dep.id}>
                      {dep.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>

          {selectedDeployment && effectiveCategory === "inference" && <ChatParamsPanel />}
          {selectedDeployment && effectiveCategory === "image" && <ImageParamsPanel />}
          {selectedDeployment && effectiveCategory === "video" && <VideoParamsPanel />}
          {selectedDeployment && effectiveCategory === "embedding" && <EmbeddingInfoPanel />}

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
                <span className="capitalize">{effectiveCategory}</span>
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

        <div className="lg:col-span-9">
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
              {effectiveCategory === "inference" && <InferencePanel deployment={selectedDeployment} />}
              {effectiveCategory === "embedding" && <EmbeddingPanel deployment={selectedDeployment} />}
              {effectiveCategory === "image" && <ImagePanel deployment={selectedDeployment} />}
              {effectiveCategory === "video" && <VideoPanel deployment={selectedDeployment} />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ChatParamsPanel() {
  const [params, setParams] = useState<ChatParams>({
    temperature: 0.7,
    max_tokens: 8192,
    top_p: 0.95,
    stream: false,
  });

  const updateParam = <K extends keyof ChatParams>(key: K, value: ChatParams[K]) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="p-3 border-b bg-muted/30">
        <h3 className="text-sm font-medium">Parameters</h3>
      </div>
      <div className="p-3 space-y-4">
        <div>
          <label className="text-xs font-medium mb-1.5 flex items-center gap-1">
            <Hash className="w-3 h-3" />
            Temperature
          </label>
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
          <label className="text-xs font-medium mb-1.5 flex items-center gap-1">
            <Maximize2 className="w-3 h-3" />
            Max Tokens
          </label>
          <input
            type="number"
            value={params.max_tokens}
            onChange={(e) => updateParam("max_tokens", parseInt(e.target.value) || 0)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          />
        </div>
        <div>
          <label className="text-xs font-medium mb-1.5 flex items-center gap-1">
            <Layers className="w-3 h-3" />
            Top P
          </label>
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
              params.stream ? "bg-ember-500" : "bg-muted"
            )}
          >
            <span className={cn("absolute top-0.5 w-4 h-4 rounded-full bg-card transition-transform", params.stream ? "left-5" : "left-0.5")} />
          </button>
        </div>
      </div>
    </div>
  );
}

function ImageParamsPanel() {
  const [params, setParams] = useState<ImageParams>({
    prompt: "",
    n: 1,
    size: "1024x1024",
    response_format: "b64_json",
    quality: "standard",
    style: "",
    scheduler: "",
    negative_prompt: "",
    seed: 0,
  });

  const updateParam = <K extends keyof ImageParams>(key: K, value: ImageParams[K]) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="p-3 border-b bg-muted/30">
        <h3 className="text-sm font-medium">Image Parameters</h3>
      </div>
      <div className="p-3 space-y-4 max-h-[400px] overflow-y-auto">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs font-medium mb-1 block">Number</label>
            <input
              type="number"
              min="1"
              max="4"
              value={params.n}
              onChange={(e) => updateParam("n", Math.min(4, Math.max(1, parseInt(e.target.value) || 1)))}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
            />
          </div>
          <div>
            <label className="text-xs font-medium mb-1 block">Size</label>
            <select
              value={params.size}
              onChange={(e) => updateParam("size", e.target.value)}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
            >
              {IMAGE_SIZES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Format</label>
          <select
            value={params.response_format}
            onChange={(e) => updateParam("response_format", e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          >
            <option value="b64_json">Base64</option>
            <option value="url">URL</option>
          </select>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Style</label>
          <select
            value={params.style || ""}
            onChange={(e) => updateParam("style", e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          >
            {STYLES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Scheduler</label>
          <select
            value={params.scheduler || ""}
            onChange={(e) => updateParam("scheduler", e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          >
            {SCHEDULERS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Seed (0 = random)</label>
          <input
            type="number"
            value={params.seed || ""}
            onChange={(e) => updateParam("seed", parseInt(e.target.value) || 0)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          />
        </div>
      </div>
    </div>
  );
}

function VideoParamsPanel() {
  const [params, setParams] = useState<VideoGenParams>({
    prompt: "",
    size: "720x1280",
    seconds: 4,
    n: 1,
    response_format: "mp4",
  });

  const updateParam = <K extends keyof VideoGenParams>(key: K, value: VideoGenParams[K]) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="p-3 border-b bg-muted/30">
        <h3 className="text-sm font-medium">Video Parameters</h3>
      </div>
      <div className="p-3 space-y-4">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs font-medium mb-1 block">Duration (s)</label>
            <input
              type="number"
              min="4"
              max="20"
              value={params.seconds}
              onChange={(e) => updateParam("seconds", Math.min(20, Math.max(4, parseInt(e.target.value) || 4)))}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
            />
          </div>
          <div>
            <label className="text-xs font-medium mb-1 block">Number</label>
            <input
              type="number"
              min="1"
              max="2"
              value={params.n}
              onChange={(e) => updateParam("n", Math.min(2, Math.max(1, parseInt(e.target.value) || 1)))}
              className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
            />
          </div>
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block">Resolution</label>
          <select
            value={params.size}
            onChange={(e) => updateParam("size", e.target.value)}
            className="w-full px-3 py-2 rounded-lg border bg-background text-sm"
          >
            {VIDEO_SIZES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}

function EmbeddingInfoPanel() {
  return (
    <div className="rounded-xl border bg-card shadow-sm">
      <div className="p-3 border-b bg-muted/30">
        <h3 className="text-sm font-medium">Info</h3>
      </div>
      <div className="p-3 text-sm text-muted-foreground text-center">
        No additional parameters for embeddings
      </div>
    </div>
  );
}

function InferencePanel({ deployment }: { deployment: Deployment }) {
  const [activeTab, setActiveTab] = useState<"chat" | "completions">("chat");
  
  return (
    <div>
      <div className="border-b">
        <div className="flex">
          <button
            onClick={() => setActiveTab("chat")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "chat" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <MessageSquare className="w-4 h-4 inline-block mr-2" />
            Chat
          </button>
          <button
            onClick={() => setActiveTab("completions")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "completions" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Bot className="w-4 h-4 inline-block mr-2" />
            Completions
          </button>
        </div>
      </div>
      <div className="p-4">
        {activeTab === "chat" && <ChatInterface deployment={deployment} />}
        {activeTab === "completions" && <CompletionsInterface deployment={deployment} />}
      </div>
    </div>
  );
}

function EmbeddingPanel({ deployment }: { deployment: Deployment }) {
  return (
    <div className="p-4">
      <EmbeddingInterface deployment={deployment} />
    </div>
  );
}

function ImagePanel({ deployment }: { deployment: Deployment }) {
  const [activeTab, setActiveTab] = useState<"generate" | "edit" | "variations">("generate");
  
  return (
    <div>
      <div className="border-b">
        <div className="flex">
          <button
            onClick={() => setActiveTab("generate")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "generate" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Sparkles className="w-4 h-4 inline-block mr-2" />
            Generate
          </button>
          <button
            onClick={() => setActiveTab("edit")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "edit" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Wand2 className="w-4 h-4 inline-block mr-2" />
            Edit
          </button>
          <button
            onClick={() => setActiveTab("variations")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "variations" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Layers className="w-4 h-4 inline-block mr-2" />
            Variations
          </button>
        </div>
      </div>
      <div className="p-4">
        {activeTab === "generate" && <ImageGenerationInterface deployment={deployment} />}
        {activeTab === "edit" && <ImageEditInterface deployment={deployment} />}
        {activeTab === "variations" && <ImageVariationInterface deployment={deployment} />}
      </div>
    </div>
  );
}

function VideoPanel({ deployment }: { deployment: Deployment }) {
  const [activeTab, setActiveTab] = useState<"generate" | "edit" | "extend">("generate");
  
  return (
    <div>
      <div className="border-b">
        <div className="flex">
          <button
            onClick={() => setActiveTab("generate")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "generate" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Sparkles className="w-4 h-4 inline-block mr-2" />
            Generate
          </button>
          <button
            onClick={() => setActiveTab("edit")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "edit" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Wand2 className="w-4 h-4 inline-block mr-2" />
            Edit
          </button>
          <button
            onClick={() => setActiveTab("extend")}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              activeTab === "extend" ? "border-ember-500 text-ember-600" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <RefreshCw className="w-4 h-4 inline-block mr-2" />
            Extend
          </button>
        </div>
      </div>
      <div className="p-4">
        {activeTab === "generate" && <VideoGenerationInterface deployment={deployment} />}
        {activeTab === "edit" && <VideoEditInterface deployment={deployment} />}
        {activeTab === "extend" && <VideoExtensionInterface deployment={deployment} />}
      </div>
    </div>
  );
}

function ChatInterface({ deployment }: { deployment: Deployment }) {
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
      const response = await fetch(`${inferenceBaseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          messages: fullMessages,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `API Error: ${response.status}`);
      }

      const data = await response.json();
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: data.choices?.[0]?.message?.content || "No response generated",
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
    <div className="flex flex-col h-[450px]">
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
            className="px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50 transition-colors"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
      </form>
    </div>
  );
}

function CompletionsInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [response, setResponse] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async () => {
    if (!prompt.trim() || isLoading) return;
    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const res = await fetch(`${inferenceBaseUrl}/v1/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
          max_tokens: 1024,
        }),
      });
      if (!res.ok) throw new Error(`API Error: ${res.status}`);
      const data = await res.json();
      setResponse(data.choices?.[0]?.text || "No response");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="flex-1 space-y-3">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Enter your prompt..."
          className="w-full h-24 px-3 py-2 rounded-lg border bg-background text-sm resize-none"
        />
        <button
          onClick={handleSubmit}
          disabled={!prompt.trim() || isLoading}
          className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50 transition-colors"
        >
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Generate"}
        </button>
        {response && (
          <div className="p-3 bg-muted rounded-lg border text-sm whitespace-pre-wrap">
            {response}
          </div>
        )}
      </div>
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
      <div className={cn("w-8 h-8 rounded-full flex items-center justify-center shrink-0", message.role === "user" ? "bg-ember-100 dark:bg-ember-900/30" : "bg-muted dark:bg-card")}>
        {message.role === "user" ? <User className="w-4 h-4 text-ember-600" /> : <Bot className="w-4 h-4 text-muted-foreground" />}
      </div>
      <div className={cn("flex-1 max-w-[85%] rounded-lg p-3", message.role === "user" ? "bg-ember-500/10 border border-ember-500/20" : "bg-muted border border-border")}>
        <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        <div className="flex justify-end mt-2">
          <button onClick={handleCopy} className="p-1 hover:bg-accent rounded">
            {copied ? <Check className="w-3 h-3 text-ember-500" /> : <Copy className="w-3 h-3 text-muted-foreground" />}
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
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({ model: deployment.modelName, input: input.trim() }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();
      const embedding = data.data?.[0]?.embedding;
      if (embedding) setEmbeddings(embedding);
      else throw new Error("No embedding returned");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
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
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Enter text to embed..." className="w-full h-24 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <div className="flex justify-between items-center">
          <button onClick={handleGenerate} disabled={!input.trim() || isLoading} className="px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Compute"} Embeddings
          </button>
          {embeddings && <span className="text-xs text-muted-foreground">Dimensions: {embeddings.length}</span>}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {embeddings ? (
          <div className="space-y-2">
            <div className="flex justify-between">
              <h4 className="text-sm font-medium">Embedding Vector</h4>
              <button onClick={handleCopy} className="text-xs">{copied ? "Copied!" : "Copy"}</button>
            </div>
            <div className="p-3 bg-muted rounded-lg border font-mono text-xs overflow-x-auto max-h-64">
              [{embeddings.slice(0, 50).map((v) => v.toFixed(6)).join(", ")}{embeddings.length > 50 && ` ... +${embeddings.length - 50} more`}]
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Database className="w-12 h-12 text-muted-foreground/20 mb-3" />
            <p className="text-sm text-muted-foreground">Enter text to generate embeddings</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ImageGenerationInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
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
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
          negative_prompt: negativePrompt.trim() || undefined,
          n: 1,
          size: "1024x1024",
          response_format: "b64_json",
        }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();
      const generatedImages = data.data?.map((img: { url?: string; b64_json?: string }) => img.url || (img.b64_json ? `data:image/png;base64,${img.b64_json}` : null)).filter(Boolean);
      if (generatedImages?.length) setImages(generatedImages);
      else throw new Error("No images returned");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the image you want to generate..." className="w-full h-20 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <textarea value={negativePrompt} onChange={(e) => setNegativePrompt(e.target.value)} placeholder="Negative prompt (what to avoid)..." className="w-full h-16 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <button onClick={handleGenerate} disabled={!prompt.trim() || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50 flex items-center justify-center gap-2">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />} Generate
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-2 gap-4">
            {images.map((img, idx) => <img key={idx} src={img} alt={`Generated ${idx + 1}`} className="rounded-lg border w-full" />)}
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

  const handleGenerate = async () => {
    if (!prompt.trim() || !image || isLoading) return;
    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/images/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({ model: deployment.modelName, prompt: prompt.trim(), image, n: 1, size: "1024x1024" }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();
      const generatedImages = data.data?.map((img: { url?: string; b64_json?: string }) => img.url || (img.b64_json ? `data:image/png;base64,${img.b64_json}` : null)).filter(Boolean);
      if (generatedImages?.length) setImages(generatedImages);
      else throw new Error("No images returned");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <div className="border-2 border-dashed rounded-lg p-4 text-center">
          <input type="file" accept="image/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => setImage((r.result as string).split(",")[1]); r.readAsDataURL(f); } }} className="hidden" id="img-edit" />
          <label htmlFor="img-edit" className="cursor-pointer"><Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" /><p className="text-xs text-muted-foreground">{image ? "Image loaded" : "Upload image"}</p></label>
        </div>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the edit..." className="w-full h-16 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <button onClick={handleGenerate} disabled={!image || !prompt.trim() || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Edit Image"}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-2 gap-4">{images.map((img, idx) => <img key={idx} src={img} alt={`Edited ${idx + 1}`} className="rounded-lg border" />)}</div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center"><Wand2 className="w-12 h-12 text-muted-foreground/20 mb-3" /><p className="text-sm text-muted-foreground">Upload an image to edit</p></div>
        )}
      </div>
    </div>
  );
}

function ImageVariationInterface({ deployment }: { deployment: Deployment }) {
  const [image, setImage] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const handleGenerate = async () => {
    if (!image || isLoading) return;
    setIsLoading(true);
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/images/variations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({ model: deployment.modelName, image, n: 1, size: "1024x1024" }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();
      const generatedImages = data.data?.map((img: { url?: string; b64_json?: string }) => img.url || (img.b64_json ? `data:image/png;base64,${img.b64_json}` : null)).filter(Boolean);
      if (generatedImages?.length) setImages(generatedImages);
      else throw new Error("No images returned");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <div className="border-2 border-dashed rounded-lg p-4 text-center">
          <input type="file" accept="image/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => setImage((r.result as string).split(",")[1]); r.readAsDataURL(f); } }} className="hidden" id="img-var" />
          <label htmlFor="img-var" className="cursor-pointer"><Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" /><p className="text-xs text-muted-foreground">{image ? "Image loaded" : "Upload image"}</p></label>
        </div>
        <button onClick={handleGenerate} disabled={!image || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Create Variation"}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-2 gap-4">{images.map((img, idx) => <img key={idx} src={img} alt={`Variation ${idx + 1}`} className="rounded-lg border" />)}</div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center"><Layers className="w-12 h-12 text-muted-foreground/20 mb-3" /><p className="text-sm text-muted-foreground">Upload an image to create variations</p></div>
        )}
      </div>
    </div>
  );
}

function VideoGenerationInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [imageRef, setImageRef] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState<string>("");

  const pollVideoStatus = async (videoId: string) => {
    const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
    const token = getToken();
    const maxAttempts = 60;
    const pollInterval = 2000;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, pollInterval));
      try {
        const response = await fetch(`${inferenceBaseUrl}/v1/videos/${videoId}?model=${encodeURIComponent(deployment.modelName)}`, {
          headers: { "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        });
        if (!response.ok) continue;
        const data = await response.json();
        const url = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
        if (url) {
          setVideoUrl(url);
          setProgress("");
          return;
        }
        if (data.status === "failed") {
          throw new Error(data.error || "Video generation failed");
        }
        setProgress(`Processing... (${attempt + 1}/${maxAttempts})`);
      } catch (e) {
        console.error("Polling error:", e);
      }
    }
    throw new Error("Video generation timed out");
  };

  const handleGenerate = async () => {
    if (!prompt.trim() || isLoading) return;
    setIsLoading(true);
    setVideoUrl(null);
    setProgress("");
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const body: Record<string, unknown> = { model: deployment.modelName, prompt: prompt.trim(), seconds: 4, n: 1 };
      if (imageRef.trim()) body.input_reference = imageRef.trim();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/generations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();

      // Check if we got immediate video (sync) or need to poll
      const immediateUrl = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
      if (immediateUrl) {
        setVideoUrl(immediateUrl);
      } else if (data.data && data.data[0]?.id) {
        // Async - poll for video
        const videoId = data.data[0].id;
        await pollVideoStatus(videoId);
      } else if (data.id) {
        await pollVideoStatus(data.id);
      } else {
        throw new Error("No video or job ID returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
      setProgress("");
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the video you want to generate..." className="w-full h-20 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <input value={imageRef} onChange={(e) => setImageRef(e.target.value)} placeholder="Image URL for image-to-video (optional)..." className="w-full px-3 py-2 rounded-lg border bg-background text-sm" disabled={isLoading} />
        <button onClick={handleGenerate} disabled={!prompt.trim() || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Generate Video"}
        </button>
        {progress && <p className="text-xs text-center text-muted-foreground">{progress}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video src={videoUrl} controls className="rounded-lg border w-full max-h-80 mx-auto" />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center"><Video className="w-12 h-12 text-muted-foreground/20 mb-3" /><p className="text-sm text-muted-foreground">Enter a prompt to generate a video</p></div>
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
  const [progress, setProgress] = useState("");

  const pollVideoStatus = async (videoId: string) => {
    const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
    const token = getToken();
    const maxAttempts = 60;
    const pollInterval = 2000;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, pollInterval));
      try {
        const response = await fetch(`${inferenceBaseUrl}/v1/videos/${videoId}?model=${encodeURIComponent(deployment.modelName)}`, {
          headers: { "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        });
        if (!response.ok) continue;
        const data = await response.json();
        const url = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
        if (url) {
          setVideoUrl(url);
          setProgress("");
          return;
        }
        if (data.status === "failed") {
          throw new Error(data.error || "Video edit failed");
        }
        setProgress(`Processing... (${attempt + 1}/${maxAttempts})`);
      } catch (e) {
        console.error("Polling error:", e);
      }
    }
    throw new Error("Video edit timed out");
  };

  const handleGenerate = async () => {
    if (!video || isLoading) return;
    setIsLoading(true);
    setVideoUrl(null);
    setProgress("");
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/edits`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({ model: deployment.modelName, prompt: prompt.trim() || undefined, video, seconds: 4 }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();

      const immediateUrl = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
      if (immediateUrl) {
        setVideoUrl(immediateUrl);
      } else if (data.data && data.data[0]?.id) {
        const videoId = data.data[0].id;
        await pollVideoStatus(videoId);
      } else if (data.id) {
        await pollVideoStatus(data.id);
      } else {
        throw new Error("No video or job ID returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
      setProgress("");
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <div className="border-2 border-dashed rounded-lg p-4 text-center">
          <input type="file" accept="video/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => setVideo((r.result as string).split(",")[1]); r.readAsDataURL(f); } }} className="hidden" id="vid-edit" />
          <label htmlFor="vid-edit" className="cursor-pointer"><Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" /><p className="text-xs text-muted-foreground">{video ? "Video loaded" : "Upload video"}</p></label>
        </div>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe the edit (optional)..." className="w-full h-16 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <button onClick={handleGenerate} disabled={!video || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Edit Video"}
        </button>
        {progress && <p className="text-xs text-center text-muted-foreground">{progress}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video src={videoUrl} controls className="rounded-lg border w-full max-h-80 mx-auto" />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center"><Wand2 className="w-12 h-12 text-muted-foreground/20 mb-3" /><p className="text-sm text-muted-foreground">Upload a video to edit</p></div>
        )}
      </div>
    </div>
  );
}

function VideoExtensionInterface({ deployment }: { deployment: Deployment }) {
  const [prompt, setPrompt] = useState("");
  const [video, setVideo] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState("");

  const pollVideoStatus = async (videoId: string) => {
    const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
    const token = getToken();
    const maxAttempts = 60;
    const pollInterval = 2000;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await new Promise(resolve => setTimeout(resolve, pollInterval));
      try {
        const response = await fetch(`${inferenceBaseUrl}/v1/videos/${videoId}?model=${encodeURIComponent(deployment.modelName)}`, {
          headers: { "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        });
        if (!response.ok) continue;
        const data = await response.json();
        const url = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
        if (url) {
          setVideoUrl(url);
          setProgress("");
          return;
        }
        if (data.status === "failed") {
          throw new Error(data.error || "Video extension failed");
        }
        setProgress(`Processing... (${attempt + 1}/${maxAttempts})`);
      } catch (e) {
        console.error("Polling error:", e);
      }
    }
    throw new Error("Video extension timed out");
  };

  const handleGenerate = async () => {
    if (!video || isLoading) return;
    setIsLoading(true);
    setVideoUrl(null);
    setProgress("");
    try {
      const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "");
      const token = getToken();
      const response = await fetch(`${inferenceBaseUrl}/v1/videos/extensions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}`, "x-sandbox": "true" },
        body: JSON.stringify({ model: deployment.modelName, prompt: prompt.trim() || undefined, video, seconds: 8 }),
      });
      if (!response.ok) throw new Error(`API Error: ${response.status}`);
      const data = await response.json();

      const immediateUrl = data.url || data.video_url || data.data?.[0]?.url || data.data?.[0]?.video_url;
      if (immediateUrl) {
        setVideoUrl(immediateUrl);
      } else if (data.data && data.data[0]?.id) {
        const videoId = data.data[0].id;
        await pollVideoStatus(videoId);
      } else if (data.id) {
        await pollVideoStatus(data.id);
      } else {
        throw new Error("No video or job ID returned");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed");
    } finally {
      setIsLoading(false);
      setProgress("");
    }
  };

  return (
    <div className="flex flex-col h-[450px]">
      <div className="p-4 border-b space-y-3">
        <div className="border-2 border-dashed rounded-lg p-4 text-center">
          <input type="file" accept="video/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => setVideo((r.result as string).split(",")[1]); r.readAsDataURL(f); } }} className="hidden" id="vid-ext" />
          <label htmlFor="vid-ext" className="cursor-pointer"><Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" /><p className="text-xs text-muted-foreground">{video ? "Video loaded" : "Upload video to extend"}</p></label>
        </div>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Describe how to extend (optional)..." className="w-full h-16 px-3 py-2 rounded-lg border bg-background text-sm resize-none" disabled={isLoading} />
        <button onClick={handleGenerate} disabled={!video || isLoading} className="w-full px-4 py-2 bg-ember-600 text-white rounded-lg hover:bg-ember-700 disabled:opacity-50">
          {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Extend Video"}
        </button>
        {progress && <p className="text-xs text-center text-muted-foreground">{progress}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video src={videoUrl} controls className="rounded-lg border w-full max-h-80 mx-auto" />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center"><RefreshCw className="w-12 h-12 text-muted-foreground/20 mb-3" /><p className="text-sm text-muted-foreground">Upload a video to extend</p></div>
        )}
      </div>
    </div>
  );
}

function getCategoryFromModelType(modelType?: string, engine?: string, workloadType?: string): ModelCategory {
  if (modelType === "embedding" || engine === "infinity" || engine === "tei") return "embedding";
  if (modelType === "video_generation" || workloadType === "video") return "video";
  if (modelType === "image_generation" || engine === "inferia-diffusion") return "image";
  return "inference";
}
