import { useState, useRef } from "react";
import { computeApi, INFERENCE_URL } from "@/lib/api";
import { getToken } from "@/lib/tokenStore";
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
} from "lucide-react";
import { cn } from "@/lib/utils";

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

type ModelCategory = "inference" | "embedding" | "image_generation" | "video_generation";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

export default function Sandbox() {
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null);
  const [category, setCategory] = useState<ModelCategory | null>(null);

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

  const effectiveCategory = category ?? (selectedDeployment ? getCategoryFromModelType(selectedDeployment.model_type, selectedDeployment.engine) : "inference");

  const handleDeploymentChange = (deployment: Deployment) => {
    setSelectedDeploymentId(deployment.id);
    const cat = getCategoryFromModelType(deployment.model_type, deployment.engine);
    setCategory(cat);
  };

  return (
    <div className="space-y-6">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Sandbox</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Test your deployed models in an interactive playground.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-4 space-y-4">
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <h3 className="text-sm font-medium mb-3">Select Deployment</h3>
            {isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                Loading deployments...
              </div>
            ) : deployments.length === 0 ? (
              <div className="text-sm text-muted-foreground flex items-center gap-2">
                <AlertCircle className="w-4 h-4" />
                No ready deployments found
              </div>
            ) : (
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {deployments.map((deployment) => (
                  <button
                    key={deployment.id}
                    onClick={() => handleDeploymentChange(deployment)}
                    className={cn(
                      "w-full text-left p-3 rounded-lg border transition-colors",
                      selectedDeployment?.id === deployment.id
                        ? "border-emerald-500 bg-emerald-500/10"
                        : "border-border hover:border-emerald-500/50 hover:bg-muted/50"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm">{deployment.name}</span>
                      <ModelTypeBadge type={deployment.model_type} engine={deployment.engine} />
                    </div>
                    <div className="text-xs text-muted-foreground mt-1 font-mono">
                      {deployment.modelName}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {selectedDeployment && (
            <div className="rounded-xl border bg-card p-4 shadow-sm">
              <h3 className="text-sm font-medium mb-3">Model Type</h3>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => setCategory("inference")}
                  className={cn(
                    "flex flex-col items-center gap-2 p-3 rounded-lg border transition-colors",
                    effectiveCategory === "inference"
                      ? "border-emerald-500 bg-emerald-500/10"
                      : "border-border hover:border-emerald-500/50"
                  )}
                >
                  <Bot className="w-5 h-5" />
                  <span className="text-xs">Chat</span>
                </button>
                <button
                  onClick={() => setCategory("embedding")}
                  className={cn(
                    "flex flex-col items-center gap-2 p-3 rounded-lg border transition-colors",
                    effectiveCategory === "embedding"
                      ? "border-emerald-500 bg-emerald-500/10"
                      : "border-border hover:border-emerald-500/50"
                  )}
                >
                  <Database className="w-5 h-5" />
                  <span className="text-xs">Embeddings</span>
                </button>
                <button
                  onClick={() => setCategory("image_generation")}
                  className={cn(
                    "flex flex-col items-center gap-2 p-3 rounded-lg border transition-colors",
                    effectiveCategory === "image_generation"
                      ? "border-emerald-500 bg-emerald-500/10"
                      : "border-border hover:border-emerald-500/50"
                  )}
                >
                  <Image className="w-5 h-5" />
                  <span className="text-xs">Image</span>
                </button>
                <button
                  onClick={() => setCategory("video_generation")}
                  className={cn(
                    "flex flex-col items-center gap-2 p-3 rounded-lg border transition-colors",
                    effectiveCategory === "video_generation"
                      ? "border-emerald-500 bg-emerald-500/10"
                      : "border-border hover:border-emerald-500/50"
                  )}
                >
                  <Video className="w-5 h-5" />
                  <span className="text-xs">Video</span>
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="lg:col-span-8">
          {!selectedDeployment ? (
            <div className="rounded-xl border bg-card p-8 shadow-sm text-center">
              <Sparkles className="w-12 h-12 mx-auto text-muted-foreground/30 mb-4" />
              <h3 className="text-lg font-medium mb-2">Select a Deployment</h3>
              <p className="text-sm text-muted-foreground">
                Choose a ready deployment from the left to start testing.
              </p>
            </div>
          ) : (
            <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
              <div className="border-b p-4 bg-muted/30">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-medium">{selectedDeployment.name}</h3>
                    <p className="text-xs text-muted-foreground font-mono">{selectedDeployment.modelName}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                    <span className="text-xs text-muted-foreground">{selectedDeployment.status}</span>
                  </div>
                </div>
              </div>

              {effectiveCategory === "inference" && <ChatInterface deployment={selectedDeployment} />}
              {effectiveCategory === "embedding" && <EmbeddingInterface deployment={selectedDeployment} />}
              {effectiveCategory === "image_generation" && <ImageGenerationInterface deployment={selectedDeployment} />}
              {effectiveCategory === "video_generation" && <VideoGenerationInterface deployment={selectedDeployment} />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ModelTypeBadge({ type, engine }: { type?: string; engine?: string }) {
  const isEmbedding = type === "embedding" || engine === "infinity" || engine === "tei";
  const isImageGen = type === "image_generation" || engine === "inferia-diffusion";
  const isVideoGen = type === "video_generation";

  if (isImageGen) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-400">
        <Image className="w-3 h-3" />
        Image
      </span>
    );
  }
  if (isVideoGen) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
        <Video className="w-3 h-3" />
        Video
      </span>
    );
  }
  if (isEmbedding) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
        <Database className="w-3 h-3" />
        Embedding
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
      <Bot className="w-3 h-3" />
      Chat
    </span>
  );
}

function ChatInterface({ deployment }: { deployment: Deployment }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

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
          messages: [...messages.map((m) => ({ role: m.role, content: m.content })), userMessage],
          stream: false,
        }),
      });

      if (!response.ok) {
        throw new Error(`API Error: ${response.status}`);
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
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Bot className="w-10 h-10 text-muted-foreground/30 mb-3" />
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
            Generating response...
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
          "flex-1 max-w-[80%] rounded-lg p-3",
          message.role === "user"
            ? "bg-emerald-500/10 border border-emerald-500/20"
            : "bg-muted border border-border"
        )}
      >
        <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        <div className="flex justify-end mt-2">
          <button
            onClick={handleCopy}
            className="p-1 hover:bg-accent rounded transition-colors"
            title="Copy"
          >
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
        throw new Error(`API Error: ${response.status}`);
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
          className="w-full h-24 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
          disabled={isLoading}
        />
        <div className="flex justify-end mt-2">
          <button
            onClick={handleGenerate}
            disabled={!input.trim() || isLoading}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center gap-2"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Generate Embeddings
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {embeddings ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium">Generated Embeddings</h4>
              <button
                onClick={handleCopy}
                className="p-1.5 hover:bg-accent rounded transition-colors inline-flex items-center gap-1 text-xs"
              >
                {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
            <div className="p-3 bg-muted rounded-lg border font-mono text-xs overflow-x-auto">
              [{embeddings.slice(0, 10).map((v) => v.toFixed(6)).join(", ")}
              {embeddings.length > 10 && (
                <span className="text-muted-foreground"> ... +{embeddings.length - 10} more</span>
              )}
              ]
            </div>
            <div className="text-xs text-muted-foreground">
              Dimensions: {embeddings.length}
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Database className="w-10 h-10 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground">Enter text above to generate embeddings</p>
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
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
          "x-sandbox": "true",
        },
        body: JSON.stringify({
          model: deployment.modelName,
          prompt: prompt.trim(),
          negative_prompt: negativePrompt.trim() || undefined,
          num_images: 1,
        }),
      });

      if (!response.ok) {
        throw new Error(`API Error: ${response.status}`);
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
        <div>
          <label className="text-xs font-medium mb-1 block">Negative Prompt (optional)</label>
          <input
            type="text"
            value={negativePrompt}
            onChange={(e) => setNegativePrompt(e.target.value)}
            placeholder="What to avoid..."
            className="w-full px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none"
            disabled={isLoading}
          />
        </div>
        <div className="flex justify-end">
          <button
            onClick={handleGenerate}
            disabled={!prompt.trim() || isLoading}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center gap-2"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            Generate Image
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {images.length > 0 ? (
          <div className="grid grid-cols-1 gap-4">
            {images.map((img, idx) => (
              <img
                key={idx}
                src={img}
                alt={`Generated ${idx + 1}`}
                className="rounded-lg border max-h-80 mx-auto"
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Image className="w-10 h-10 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground">Enter a prompt to generate an image</p>
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
      const response = await fetch(`${inferenceBaseUrl}/v1/video/generations`, {
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
        throw new Error(`API Error: ${response.status}`);
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
      <div className="p-4 border-b">
        <label className="text-xs font-medium mb-1 block">Prompt</label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the video you want to generate..."
          className="w-full h-24 px-3 py-2 rounded-lg border bg-background focus:ring-1 focus:ring-emerald-500 outline-none resize-none"
          disabled={isLoading}
        />
        <div className="flex justify-end mt-2">
          <button
            onClick={handleGenerate}
            disabled={!prompt.trim() || isLoading}
            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors inline-flex items-center gap-2"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Video className="w-4 h-4" />}
            Generate Video
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {videoUrl ? (
          <video
            src={videoUrl}
            controls
            className="rounded-lg border max-h-80 mx-auto"
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Video className="w-10 h-10 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground">Enter a prompt to generate a video</p>
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
